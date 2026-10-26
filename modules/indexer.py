"""
Incremental indexing for the Project Intelligence Platform.

Run from the project root:
    python -m modules.indexer

Reads CSV, PDF and DOCX files from ./data and ./documents (if present),
creates Gemini embeddings, and stores vectors + text/metadata in Qdrant Cloud.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from google import genai
from qdrant_client import QdrantClient, models

from modules.file_readers import read_file

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
LOG = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
VECTOR_SIZE = int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "3072"))
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "ai_agent_demo_intelligence")
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "2500"))  # characters, not tokens
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "300"))
EMBED_BATCH = int(os.getenv("RAG_EMBEDDING_BATCH_SIZE", "32"))
UPLOAD_BATCH = int(os.getenv("QDRANT_UPLOAD_BATCH_SIZE", "64"))
SUPPORTED_EXTENSIONS = {".csv", ".pdf", ".docx"}

# Prefer a source-specific key. Do not use project_id as the unique key for risks
# or milestones: one project can legitimately have multiple risks/milestones.
PREFERRED_ID_BY_FILENAME = {
    "projects.csv": ("project_id", "record_id", "id"),
    "milestones.csv": ("milestone_id", "record_id", "id"),
    "risks.csv": ("risk_id", "issue_id", "record_id", "id"),
}
FALLBACK_ID_COLUMNS = ("record_id", "risk_id", "milestone_id", "issue_id", "id", "project_id")


def _validate() -> None:
    if VECTOR_SIZE <= 0:
        raise ValueError("GEMINI_EMBEDDING_DIMENSIONS must be positive.")
    if CHUNK_SIZE <= 0:
        raise ValueError("RAG_CHUNK_SIZE must be positive.")
    if not 0 <= CHUNK_OVERLAP < CHUNK_SIZE:
        raise ValueError("RAG_CHUNK_OVERLAP must be >= 0 and smaller than RAG_CHUNK_SIZE.")
    if EMBED_BATCH < 1 or UPLOAD_BATCH < 1:
        raise ValueError("Embedding and upload batch sizes must be positive.")


def _clients() -> tuple[genai.Client, QdrantClient]:
    gemini_key = os.getenv("GEMINI_API_KEY")
    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_key = os.getenv("QDRANT_API_KEY")
    missing = [
        name for name, value in (
            ("GEMINI_API_KEY", gemini_key),
            ("QDRANT_URL", qdrant_url),
            ("QDRANT_API_KEY", qdrant_key),
        ) if not value
    ]
    if missing:
        raise ValueError("Missing required .env setting(s): " + ", ".join(missing))
    return (
        genai.Client(api_key=gemini_key),
        QdrantClient(url=qdrant_url, api_key=qdrant_key),
    )


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source(path: Path) -> str:
    """Store paths consistently relative to the project root when possible."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _point_id(source: str, key: str, chunk_index: int = 0) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}|{key}|{chunk_index}"))


def _ensure_collection(client: QdrantClient, name: str) -> None:
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=VECTOR_SIZE,
                distance=models.Distance.COSINE,
            ),
        )
    else:
        config = client.get_collection(name).config.params.vectors
        if isinstance(config, dict):
            raise ValueError(
                f"Collection {name!r} uses named vectors. Configure a separate collection for this indexer."
            )
        existing_size = getattr(config, "size", None)
        if existing_size != VECTOR_SIZE:
            raise ValueError(
                f"Collection {name!r} has vector size {existing_size}; this indexer expects {VECTOR_SIZE}. "
                "Use a new collection or configure the matching embedding dimensions. Existing data was not changed."
            )

    # Create payload indexes before any scroll/filter operation. Some Qdrant
    # Cloud clusters use strict mode and reject filtered scrolls without them.
    existing_indexes = client.get_collection(name).payload_schema or {}
    for field in ("source", "record_key"):
        if field not in existing_indexes:
            client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )


def _scroll_source(client: QdrantClient, collection: str, source: str) -> list[models.Record]:
    records: list[models.Record] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="source", match=models.MatchValue(value=source))]
            ),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        records.extend(batch)
        if offset is None:
            break
    return records


def _chunk_text(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + CHUNK_SIZE, length)
        if end < length:
            # Prefer a paragraph or sentence boundary near the end of the chunk.
            candidates = [
                text.rfind("\n\n", start, end),
                text.rfind("\n", start, end),
                text.rfind(". ", start, end),
            ]
            boundary = max(candidates)
            if boundary > start + int(CHUNK_SIZE * 0.55):
                end = boundary + (2 if text[boundary:boundary + 2] == ". " else 1)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _embed(client: genai.Client, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start:start + EMBED_BATCH]
        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config={
                "task_type": "RETRIEVAL_DOCUMENT",
                "output_dimensionality": VECTOR_SIZE,
            },
        )
        embeddings = getattr(response, "embeddings", None) or []
        if len(embeddings) != len(batch):
            raise RuntimeError(
                f"Gemini returned {len(embeddings)} embeddings for {len(batch)} text chunks."
            )
        for embedding in embeddings:
            values = getattr(embedding, "values", None)
            if values is None:
                raise RuntimeError("Gemini returned an embedding without vector values.")
            vectors.append(list(values))
    return vectors


def _safe_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, TypeError):
            pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _csv_records(df: pd.DataFrame, filename: str) -> list[dict[str, Any]]:
    if df.empty:
        return []

    # Map normalized header names back to their original column names.
    normalized = {str(column).strip().lower(): str(column) for column in df.columns}
    filename_key = Path(filename).name.lower()
    preferred = PREFERRED_ID_BY_FILENAME.get(filename_key, ())
    # For known datasets, use their own entity ID first. In particular, risks.csv
    # has multiple risks per project, so project_id must never win over risk_id.
    if preferred:
        id_col = next((normalized[c] for c in preferred if c in normalized), None)
    else:
        id_col = next((normalized[c] for c in FALLBACK_ID_COLUMNS if c in normalized), None)

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row_num, raw in enumerate(df.to_dict(orient="records"), start=1):
        row = {str(k): _safe_value(v) for k, v in raw.items()}
        row = {k: v for k, v in row.items() if v is not None}
        if not row:
            continue

        if id_col is not None:
            raw_id = row.get(id_col)
            if raw_id is None or not str(raw_id).strip():
                raise ValueError(f"CSV row {row_num} has an empty ID in column {id_col!r}.")
            key = f"{id_col.strip().lower()}:{str(raw_id).strip()}"
        else:
            # Row-number fallback is deterministic only while row ordering is stable.
            key = f"row:{row_num}"

        if key in seen:
            raise ValueError(
                f"Duplicate CSV record key {key!r} in {filename}. "
                "The selected ID column must contain unique values."
            )
        seen.add(key)
        text = "\n".join(f"{k}: {v}" for k, v in row.items())
        records.append({
            "key": key,
            "text": text,
            "hash": _sha_text(text),
            "metadata": {
                "row_number": row_num,
                "record_id": str(row.get(id_col, "")) if id_col else "",
                "project_id": str(row.get("project_id", "")),
                "id_column": id_col or "",
            },
        })
    return records


def _upload_points(client: QdrantClient, collection: str, points: list[models.PointStruct]) -> None:
    for start in range(0, len(points), UPLOAD_BATCH):
        client.upsert(
            collection_name=collection,
            points=points[start:start + UPLOAD_BATCH],
            wait=True,
        )


def _delete_ids(client: QdrantClient, collection: str, ids: list[str]) -> None:
    for start in range(0, len(ids), UPLOAD_BATCH):
        client.delete(
            collection_name=collection,
            points_selector=models.PointIdsList(points=ids[start:start + UPLOAD_BATCH]),
            wait=True,
        )


def _index_csv(
    path: Path, source: str, file_hash: str, qdrant: QdrantClient,
    gemini: genai.Client, collection: str,
) -> dict[str, int]:
    df = pd.read_csv(path)
    records = _csv_records(df, path.name)
    old_points = _scroll_source(qdrant, collection, source)
    old_by_key = {
        str((point.payload or {}).get("record_key", "")): point
        for point in old_points
    }
    new_keys = {record["key"] for record in records}
    changed = [
        record for record in records
        if (record["key"] not in old_by_key)
        or ((old_by_key[record["key"]].payload or {}).get("content_hash") != record["hash"])
    ]

    vectors = _embed(gemini, [record["text"] for record in changed]) if changed else []
    points: list[models.PointStruct] = []
    for record, vector in zip(changed, vectors):
        points.append(models.PointStruct(
            id=_point_id(source, record["key"]),
            vector=vector,
            payload={
                "source": source,
                "file_hash": file_hash,
                "record_key": record["key"],
                "text": record["text"],
                "content_hash": record["hash"],
                "file_type": ".csv",
                "embedding_model": EMBEDDING_MODEL,
                **record["metadata"],
            },
        ))
    if points:
        _upload_points(qdrant, collection, points)

    deleted_ids = [
        str(point.id) for key, point in old_by_key.items()
        if key not in new_keys
    ]
    if deleted_ids:
        _delete_ids(qdrant, collection, deleted_ids)

    return {
        "indexed": len(changed),
        "skipped": len(records) - len(changed),
        "deleted": len(deleted_ids),
    }


def _index_document(
    path: Path, source: str, file_hash: str, qdrant: QdrantClient,
    gemini: genai.Client, collection: str,
) -> dict[str, int]:
    old_points = _scroll_source(qdrant, collection, source)
    if old_points and all(
        (point.payload or {}).get("file_hash") == file_hash for point in old_points
    ):
        return {"indexed": 0, "skipped": len(old_points), "deleted": 0}

    text = read_file(path)
    chunks = _chunk_text(text)
    vectors = _embed(gemini, chunks) if chunks else []
    points = [
        models.PointStruct(
            id=_point_id(source, "document", index),
            vector=vector,
            payload={
                "source": source,
                "file_hash": file_hash,
                "record_key": f"document:{index}",
                "chunk_index": index,
                "text": chunk,
                "content_hash": _sha_text(chunk),
                "file_type": path.suffix.lower(),
                "embedding_model": EMBEDDING_MODEL,
            },
        )
        for index, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]

    # Embed all chunks before updating the existing version.
    if points:
        _upload_points(qdrant, collection, points)

    expected_ids = {_point_id(source, "document", index) for index in range(len(points))}
    stale_ids = [str(point.id) for point in old_points if str(point.id) not in expected_ids]
    if stale_ids:
        _delete_ids(qdrant, collection, stale_ids)

    if not points and old_points:
        _delete_ids(qdrant, collection, [str(point.id) for point in old_points])

    return {"indexed": len(points), "skipped": 0, "deleted": len(stale_ids)}


def index_knowledge_base(
    data_dir: str = "data",
    documents_dir: str = "documents",
    collection_name: str = COLLECTION_NAME,
) -> dict[str, Any]:
    """Index local CSV/PDF/DOCX sources incrementally into Qdrant Cloud."""
    _validate()
    roots = [Path(data_dir), Path(documents_dir)]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(
                path.resolve() for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
            )
    files = sorted(set(files))
    if not files:
        raise FileNotFoundError(
            f"No CSV, PDF or DOCX files found in {data_dir!r} or {documents_dir!r}."
        )

    gemini, qdrant = _clients()
    summary: dict[str, Any] = {
        "status": "success",
        "files_found": len(files),
        "files_processed": 0,
        "files_skipped": 0,
        "records_or_chunks_indexed": 0,
        "records_or_chunks_skipped": 0,
        "records_or_chunks_deleted": 0,
        "collection": collection_name,
        "embedding_model": EMBEDDING_MODEL,
        "details": [],
        "errors": [],
    }

    try:
        _ensure_collection(qdrant, collection_name)
        for path in files:
            source = _source(path)
            try:
                file_hash = _sha_file(path)
                if path.suffix.lower() == ".csv":
                    result = _index_csv(path, source, file_hash, qdrant, gemini, collection_name)
                else:
                    result = _index_document(path, source, file_hash, qdrant, gemini, collection_name)

                summary["files_processed"] += 1
                summary["records_or_chunks_indexed"] += result["indexed"]
                summary["records_or_chunks_skipped"] += result["skipped"]
                summary["records_or_chunks_deleted"] += result["deleted"]
                if result["indexed"] == 0 and result["skipped"] > 0:
                    summary["files_skipped"] += 1
                summary["details"].append({"source": source, **result})
                LOG.info(
                    "%s: indexed=%s skipped=%s deleted=%s",
                    source, result["indexed"], result["skipped"], result["deleted"],
                )
            except Exception as exc:
                LOG.exception("Failed to index %s", source)
                summary["errors"].append({"source": source, "error": str(exc)})

        if summary["errors"]:
            summary["status"] = "partial_failure"
    finally:
        qdrant.close()

    return summary


if __name__ == "__main__":
    # The user's current layout has all 8 source files in ./data.
    print(json.dumps(
        index_knowledge_base(data_dir="data", documents_dir="data"),
        indent=2,
        ensure_ascii=False,
    ))

"""
Standalone semantic retrieval test for Project Intelligence Platform.

Run from the project root:
    python test_retrieval.py
"""

import os

from dotenv import load_dotenv
from google import genai
from qdrant_client import QdrantClient

load_dotenv()

EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
VECTOR_SIZE = int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "3072"))
COLLECTION_NAME = os.getenv(
    "QDRANT_COLLECTION",
    "ai_agent_demo_intelligence",
)


def main() -> None:
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")

    if not gemini_api_key:
        raise ValueError("GEMINI_API_KEY is missing from .env")
    if not qdrant_url or not qdrant_api_key:
        raise ValueError("QDRANT_URL or QDRANT_API_KEY is missing from .env")

    question = input(
        "Ask a question about your project documents "
        "(e.g. What risks could delay the migration?): "
    ).strip()

    if not question:
        print("No question entered. Exiting.")
        return

    # 1) Convert the question into a vector using the same model/dimensions
    #    used by the indexer. RETRIEVAL_QUERY is for search queries.
    gemini = genai.Client(api_key=gemini_api_key)
    response = gemini.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=question,
        config={
            "task_type": "RETRIEVAL_QUERY",
            "output_dimensionality": VECTOR_SIZE,
        },
    )

    if not response.embeddings or not response.embeddings[0].values:
        raise RuntimeError("Gemini returned no query embedding.")

    query_vector = response.embeddings[0].values

    # 2) Search the Qdrant collection for semantically similar chunks.
    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    if not qdrant.collection_exists(COLLECTION_NAME):
        raise RuntimeError(
            f"Qdrant collection '{COLLECTION_NAME}' does not exist. "
            "Check QDRANT_COLLECTION in .env and your indexer configuration."
        )

    results = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=5,
        with_payload=True,
    ).points

    if not results:
        print("\nNo matching chunks found.")
        return

    # 3) Print the retrieved chunks and metadata so we can judge relevance.
    print(f"\nCollection: {COLLECTION_NAME}")
    print(f"Question: {question}")
    print(f"Top {len(results)} semantic search results:\n")

    for rank, point in enumerate(results, start=1):
        payload = point.payload or {}
        print("=" * 80)
        print(f"Result #{rank} | Similarity score: {point.score:.4f}")
        print(f"Source: {payload.get('source', 'unknown')}")
        print(f"Record key: {payload.get('record_key', 'unknown')}")
        if payload.get("project_id"):
            print(f"Project ID: {payload['project_id']}")
        if payload.get("chunk_index") is not None:
            print(f"Chunk index: {payload['chunk_index']}")
        print("\nText:")
        print(payload.get("text", "[No text field in payload]"))
        print()

    print("Retrieval test finished. Review whether the returned sources/text answer the question.")


if __name__ == "__main__":
    main()

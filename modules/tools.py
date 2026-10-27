"""Tools available to the Project Intelligence AI Agent.

Run from the project root. Structured tools read CSV files locally; document
search reuses the existing Gemini + Qdrant retrieval implementation in
modules/chatbot.py, so the documents do not need to be indexed again.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def _read_csv(filename: str) -> pd.DataFrame:
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Required data file not found: {path}")
    return pd.read_csv(path)


def get_project_portfolio() -> dict[str, Any]:
    """Return structured project portfolio records from projects.csv."""
    df = _read_csv("projects.csv")
    return {
        "source": "projects.csv",
        "total_projects": int(len(df)),
        "columns": df.columns.tolist(),
        "projects": df.fillna("").to_dict(orient="records"),
    }


def get_open_risks() -> dict[str, Any]:
    """Return risk records, excluding closed/resolved risks when status exists."""
    df = _read_csv("risks.csv")
    status_col = next(
        (c for c in df.columns if c.strip().lower() in {"status", "risk_status"}),
        None,
    )
    if status_col:
        closed_values = {"closed", "resolved", "complete", "completed"}
        df = df[
            ~df[status_col].astype(str).str.strip().str.lower().isin(closed_values)
        ]
    return {
        "source": "risks.csv",
        "open_risk_count": int(len(df)),
        "columns": df.columns.tolist(),
        "risks": df.fillna("").to_dict(orient="records"),
    }


def get_milestones() -> dict[str, Any]:
    """Return milestone records from milestones.csv."""
    df = _read_csv("milestones.csv")
    return {
        "source": "milestones.csv",
        "total_milestones": int(len(df)),
        "columns": df.columns.tolist(),
        "milestones": df.fillna("").to_dict(orient="records"),
    }


def search_project_documents(query: str, top_k: int = 5) -> dict[str, Any]:
    """Search the existing Qdrant index for relevant CSV/PDF/DOCX evidence."""
    from modules.chatbot import retrieve_context

    results = retrieve_context(query, top_k=top_k)
    return {
        "query": query,
        "result_count": len(results),
        "evidence": [
            {
                "source": item.get("source", "Unknown source"),
                "record_key": item.get("record_key", ""),
                "project_id": item.get("project_id", ""),
                "similarity_score": item.get("score", 0.0),
                "content": item.get("text", ""),
            }
            for item in results
        ],
    }


def run_tool(tool_name: str, query: str = "") -> dict[str, Any]:
    """Dispatch a named tool; intended for controlled agent orchestration."""
    if tool_name == "get_project_portfolio":
        return get_project_portfolio()
    if tool_name == "get_open_risks":
        return get_open_risks()
    if tool_name == "get_milestones":
        return get_milestones()
    if tool_name == "search_project_documents":
        if not query.strip():
            return {"error": "A non-empty search query is required."}
        return search_project_documents(query)
    return {"error": f"Unknown tool: {tool_name}"}

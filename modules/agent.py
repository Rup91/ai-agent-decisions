"""Reliable, budget-aware Project Intelligence Agent v4.1.

Normal successful path:
- Read portfolio, open risks and milestones locally (no LLM calls).
- Make one Groq request to plan a focused document search.
- Retrieve a bounded set of evidence from the existing Gemini + Qdrant index.
- Make one Groq request to write the report.
- Deterministically append a project coverage audit so the model cannot silently omit
  portfolio project IDs from the final output.

This module preserves the existing public API: run_agent(goal, max_rounds=2).
Version 4.1 fixes a critical bug where oversized payloads were reduced to top-level keys.
Place it at modules/agent.py after backing up the current file.
"""
from __future__ import annotations

import html
import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from groq import Groq

from modules.tools import get_milestones, get_open_risks, get_project_portfolio

load_dotenv()

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_DOCUMENT_HITS = 5
MAX_STRUCTURED_CHARS = 9_000
MAX_EVIDENCE_CHARS = 5_000
MAX_REPORT_INPUT_CHARS = 14_000
QUERY_MAX_TOKENS = 180
REPORT_MAX_TOKENS = 1_500

QUERY_SYSTEM_PROMPT = """You are a project-evidence search planner.
Create ONE concise search query for the existing indexed project knowledge base.
Use only project IDs and topics present in the supplied data. Prioritize red/amber
projects, high/critical open risks, delayed milestones, dependencies, architecture/status
evidence, and any project with incomplete structured coverage.
Return only the query, no explanation, markdown, or JSON. Do not invent facts.
"""

REPORT_SYSTEM_PROMPT = """You are a read-only Project Intelligence Agent preparing a senior-management report.
Use only the supplied structured data and retrieved document evidence.
Do not invent facts, dates, owners, costs, decisions, or citations.
Missing data is an evidence gap, not proof a project is healthy.
Review EVERY project in the portfolio. Include every project whose overall status is
RED or AMBER, has an open high/critical risk, has a delayed milestone, or has material
evidence gaps. Distinguish portfolio status from risk status. Describe source conflicts.
Tie each recommendation to evidence. Cite source filenames and IDs when supplied.
Use these Markdown sections:
1. Executive Summary
2. At-Risk Projects
3. Findings & Evidence
4. Risks & Impacts
5. Top Three Management Actions
6. Evidence Gaps / Conflicts
7. Coverage Check
Do not output raw HTML tags. Keep the report concise and readable.
"""


def _compact_json(value: Any, max_chars: int) -> str:
    """Serialize valid JSON within budget while preserving every project/risk/milestone row.

    The previous implementation's last-resort fallback returned only top-level keys.
    That made the model claim the evidence package contained no records. This version
    progressively trims text and nonessential fields, but never replaces actual records
    with a keys-only placeholder.
    """
    def dump(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"))

    raw = dump(value)
    if len(raw) <= max_chars:
        return raw

    def shorten_strings(obj: Any, limit: int) -> Any:
        if isinstance(obj, dict):
            return {k: shorten_strings(v, limit) for k, v in obj.items()}
        if isinstance(obj, list):
            return [shorten_strings(v, limit) for v in obj]
        if isinstance(obj, str) and len(obj) > limit:
            return obj[:limit] + "…"
        return obj

    # First keep the full schema and all records, trimming long free-text values.
    for limit in (1200, 700, 400, 220, 120, 70, 35):
        candidate = dump(shorten_strings(value, limit))
        if len(candidate) <= max_chars:
            return candidate

    # If still large, retain all rows but keep the most useful columns in each row.
    priority_terms = (
        "project_id", "projectid", "project id", "name", "title", "status", "severity",
        "risk_id", "riskid", "risk id", "milestone_id", "milestoneid", "milestone id",
        "owner", "manager", "date", "due", "forecast", "baseline", "delay", "impact",
        "likelihood", "description", "summary", "dependency", "source", "record_key",
        "score", "content", "mitigation", "budget", "schedule", "cost", "priority",
    )

    def prune(obj: Any) -> Any:
        if isinstance(obj, dict):
            # Keep metadata/counts/goal/instructions and selectively trim record dictionaries.
            out = {}
            for key, item in obj.items():
                if isinstance(item, list) and item and all(isinstance(x, dict) for x in item):
                    rows = []
                    for row in item:
                        selected = {
                            k: shorten_strings(v, 180)
                            for k, v in row.items()
                            if any(term in str(k).lower().replace(" ", "_") for term in priority_terms)
                        }
                        if not selected:
                            # Preserve a small amount of each row if headers are unusual.
                            selected = {k: shorten_strings(v, 80) for k, v in list(row.items())[:4]}
                        rows.append(selected)
                    out[key] = rows
                elif isinstance(item, (dict, list)):
                    out[key] = prune(item)
                else:
                    out[key] = shorten_strings(item, 250)
            return out
        if isinstance(obj, list):
            return [prune(item) for item in obj]
        return shorten_strings(obj, 250)

    candidate_obj = prune(value)
    candidate = dump(candidate_obj)
    if len(candidate) <= max_chars:
        return candidate

    # Final reduction: keep all record IDs and row counts, not just top-level keys.
    # Each row is represented individually so the model can still see which projects exist.
    def row_index(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for key, item in obj.items():
                if isinstance(item, list) and item and all(isinstance(x, dict) for x in item):
                    rows = []
                    for row in item:
                        compact_row = {}
                        for k, v in row.items():
                            kn = str(k).lower().replace(" ", "_")
                            if any(t in kn for t in ("project_id", "projectid", "risk_id", "milestone_id", "status", "severity", "name", "title")):
                                compact_row[k] = shorten_strings(v, 80)
                        rows.append(compact_row or {"record_fields": list(row.keys())[:8]})
                    out[key] = rows
                elif isinstance(item, (dict, list)):
                    out[key] = row_index(item)
                else:
                    out[key] = shorten_strings(item, 100)
            return out
        if isinstance(obj, list):
            return [row_index(x) for x in obj]
        return shorten_strings(obj, 100)

    candidate = dump(row_index(value))
    # This is a last-resort bounded prompt. In ordinary project CSVs it should fit.
    # Never return malformed JSON and never intentionally erase the record arrays.
    if len(candidate) > max_chars:
        raise ValueError(
            f"Evidence payload is too large to safely fit the model context "
            f"({len(candidate)} characters after record-preserving compaction; limit {max_chars}). "
            "Reduce verbose CSV columns or raise the context budget."
        )
    return candidate


def _normalise_records(result: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or not isinstance(result.get(key), list):
        return []
    return [row for row in result[key] if isinstance(row, dict)]


def _get_value(row: dict[str, Any], *candidates: str) -> Any:
    wanted = {c.strip().lower().replace(" ", "_") for c in candidates}
    for key, value in row.items():
        normalised = str(key).strip().lower().replace(" ", "_")
        if normalised in wanted:
            return value
    return ""


def _project_id(row: dict[str, Any]) -> str:
    return str(_get_value(row, "project_id", "project id", "projectid")).strip()


def _build_snapshot() -> dict[str, Any]:
    """Read all structured sources locally and build deterministic coverage metadata."""
    portfolio_result = get_project_portfolio()
    risk_result = get_open_risks()
    milestone_result = get_milestones()

    projects = _normalise_records(portfolio_result, "projects")
    risks = _normalise_records(risk_result, "risks")
    milestones = _normalise_records(milestone_result, "milestones")

    project_ids = list(dict.fromkeys(pid for pid in (_project_id(p) for p in projects) if pid))
    risk_ids = {_project_id(r) for r in risks if _project_id(r)}
    milestone_ids = {_project_id(m) for m in milestones if _project_id(m)}

    coverage = []
    for project in projects:
        pid = _project_id(project)
        if not pid:
            continue
        coverage.append({
            "project_id": pid,
            "overall_status": str(_get_value(project, "overall_status", "overall status")).strip(),
            "risk_status": str(_get_value(project, "risk_status", "risk status")).strip(),
            "has_open_risk_record": pid in risk_ids,
            "has_milestone_record": pid in milestone_ids,
        })

    return {
        "sources": {
            "portfolio": portfolio_result.get("source", "projects.csv"),
            "risks": risk_result.get("source", "risks.csv"),
            "milestones": milestone_result.get("source", "milestones.csv"),
        },
        "projects": projects,
        "open_risks": risks,
        "milestones": milestones,
        "coverage_check": coverage,
        "project_ids": project_ids,
        "project_count": len(projects),
        "open_risk_count": len(risks),
        "milestone_count": len(milestones),
    }


def _fallback_query(goal: str, snapshot: dict[str, Any]) -> str:
    ids = ", ".join(snapshot.get("project_ids", []))
    risk_ids = ", ".join(
        str(_get_value(risk, "risk_id", "risk id")) for risk in snapshot.get("open_risks", [])
    )
    milestone_ids = ", ".join(
        str(_get_value(item, "milestone_id", "milestone id")) for item in snapshot.get("milestones", [])
    )
    return (
        f"{goal}. Projects: {ids}. Risks: {risk_ids}. Milestones: {milestone_ids}. "
        "Find supporting status reports, dependencies, architecture evidence, forecast dates, "
        "and missing or conflicting evidence for each listed project."
    )[:1200]


def _plan_query(client: Groq, goal: str, snapshot: dict[str, Any]) -> str:
    planner_input = {
        "goal": goal,
        "projects": snapshot["projects"],
        "open_risks": snapshot["open_risks"],
        "milestones": snapshot["milestones"],
        "coverage_check": snapshot["coverage_check"],
    }
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": QUERY_SYSTEM_PROMPT},
            {"role": "user", "content": "Create one targeted search query from this data:\n" +
             _compact_json(planner_input, MAX_STRUCTURED_CHARS)},
        ],
        temperature=0.1,
        max_tokens=QUERY_MAX_TOKENS,
    )
    query = (response.choices[0].message.content or "").strip()
    return query[:1200] if query else _fallback_query(goal, snapshot)


def _search_documents(query: str) -> dict[str, Any]:
    """Retrieve bounded evidence from the existing index; handle empty results cleanly."""
    from modules.chatbot import retrieve_context

    hits = retrieve_context(query, top_k=MAX_DOCUMENT_HITS) or []
    evidence = []
    for hit in hits:
        evidence.append({
            "source": hit.get("source", "Unknown source"),
            "record_key": hit.get("record_key", ""),
            "project_id": hit.get("project_id", ""),
            "similarity_score": hit.get("score", 0.0),
            "content": str(hit.get("text", ""))[:850],
        })
    return {"query": query, "result_count": len(evidence), "evidence": evidence}


def _clean_report_markup(report: str) -> str:
    """Remove common raw HTML artifacts while preserving ordinary text."""
    cleaned = html.unescape(report or "")
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</?(?:p|div|span)\b[^>]*>", "", cleaned)
    cleaned = re.sub(r"(?i)<[^>]+>", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _append_coverage_audit(report: str, snapshot: dict[str, Any]) -> str:
    """Append a deterministic project inventory, regardless of LLM omissions."""
    projects = snapshot.get("projects", [])
    coverage = {item["project_id"]: item for item in snapshot.get("coverage_check", [])}
    if not projects:
        return report + "\n\n## Deterministic Coverage Audit\nNo portfolio project records were returned."

    lines = [
        "",
        "## Deterministic Coverage Audit",
        f"Portfolio records loaded: **{len(projects)}**",
        "",
        "| Project ID | Portfolio status | Risk status | Open risk record | Milestone record |",
        "|---|---|---|---|---|",
    ]
    for project in projects:
        pid = _project_id(project) or "ID missing"
        item = coverage.get(pid, {})
        status = str(item.get("overall_status", "Not available")).strip() or "Not available"
        risk_status = str(item.get("risk_status", "Not available")).strip() or "Not available"
        risk_record = "Present" if item.get("has_open_risk_record") else "Not found"
        milestone_record = "Present" if item.get("has_milestone_record") else "Not found"
        lines.append(f"| {pid} | {status} | {risk_status} | {risk_record} | {milestone_record} |")

    lines.extend([
        "",
        "This audit verifies structured-record coverage only; it does not prove that document evidence is complete.",
    ])
    return report.rstrip() + "\n" + "\n".join(lines)


def _write_report(client: Groq, goal: str, snapshot: dict[str, Any], evidence: dict[str, Any]) -> str:
    report_input = {
        "business_goal": goal,
        "structured_snapshot": snapshot,
        "document_search": evidence,
        "instructions": [
            "Include each project with RED/AMBER portfolio status, high/critical open risk, delayed milestone, or evidence gap.",
            "Separate overall_status from risk_status; do not label a project GREEN solely because its overall status is GREEN.",
            "Mention every project ID in the Coverage Check.",
            "Use source filenames and risk/milestone IDs when present.",
            "If document search returns no hits, explicitly state that document evidence was unavailable.",
        ],
    }
    prompt = _compact_json(report_input, MAX_REPORT_INPUT_CHARS)
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": REPORT_SYSTEM_PROMPT},
            {"role": "user", "content": "Write the executive report using only this evidence package:\n" + prompt},
        ],
        temperature=0.1,
        max_tokens=REPORT_MAX_TOKENS,
    )
    return _clean_report_markup((response.choices[0].message.content or "").strip())


def run_agent(goal: str, max_rounds: int = 2) -> dict[str, Any]:
    """Run v4; normal path uses two Groq LLM requests plus one retrieval."""
    goal = (goal or "").strip()
    if not goal:
        return {"report": "Please enter a business goal.", "tool_log": [], "status": "invalid"}
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {"report": "GROQ_API_KEY is missing from .env.", "tool_log": [], "status": "error"}

    tool_log: list[dict[str, Any]] = []
    try:
        snapshot = _build_snapshot()
        tool_log.extend([
            {"tool": "get_project_portfolio", "result_status": "ok", "execution": "local_csv",
             "records": len(snapshot["projects"])},
            {"tool": "get_open_risks", "result_status": "ok", "execution": "local_csv",
             "records": len(snapshot["open_risks"])},
            {"tool": "get_milestones", "result_status": "ok", "execution": "local_csv",
             "records": len(snapshot["milestones"])},
        ])

        client = Groq(api_key=api_key)

        # Groq call 1: plan one targeted search query.
        query = _plan_query(client, goal, snapshot)
        evidence = _search_documents(query)
        tool_log.append({
            "tool": "search_project_documents",
            "query": query,
            "result_status": "ok" if evidence.get("result_count", 0) else "empty",
            "execution": "qdrant_retrieval",
            "result_count": evidence.get("result_count", 0),
        })

        # Groq call 2: synthesize. Always append the deterministic inventory after generation.
        report = _write_report(client, goal, snapshot, evidence)
        if not report:
            report = "The model returned an empty report."
        report = _append_coverage_audit(report, snapshot)
        return {"report": report, "tool_log": tool_log, "status": "success"}

    except Exception as exc:
        return {
            "report": f"Agent execution failed: {type(exc).__name__}: {exc}\n"
                      "No project data was changed. Check provider availability/rate limits and try again.",
            "tool_log": tool_log,
            "status": "error",
        }


if __name__ == "__main__":
    goal = (
        "Investigate all at-risk projects for the coming month. Cross-check portfolio status, "
        "open risks, milestone slippage, dependencies, and supporting project documents. "
        "Recommend the top three Senior Management actions and flag missing or conflicting evidence."
    )
    result = run_agent(goal)
    print("\n=== AGENT REPORT ===\n")
    print(result["report"])
    print("\n=== TOOL CALLS ===\n")
    print(json.dumps(result["tool_log"], ensure_ascii=False, indent=2))
    print(f"\nStatus: {result['status']}")

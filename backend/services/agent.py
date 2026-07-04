"""
Agentic assistant for RadHarness.

A local tool-using agent. It runs a small reason-act loop against the LOCAL
model and can call tools that search the worklist, search the local knowledge
base (RAG), run critical-findings triage, and generate an impression. All tools
execute on the machine. No data leaves the device.

The agent is a productivity aid, not a medical device. Every reply carries
config.DISCLAIMER.
"""
from __future__ import annotations

import json
from typing import Any

from backend import config, db
from backend.llm import client
from backend.schemas import ChatRequest, ChatResponse, ImpressionRequest, ToolCallTrace

MAX_STEPS = 4

_SYSTEM = (
    "You are RadHarness Assistant, a local AI aide for a radiologist. You run "
    "fully on the radiologist's machine and must keep all patient data local. "
    "Be precise, concise, and clinical. Use the available tools when they help: "
    "search the worklist, search the local knowledge base for guidelines, run "
    "triage on report text, or generate an impression. When you use knowledge "
    "base results, ground your answer in them and mention the source title. "
    "Never fabricate findings, measurements, or citations. You are a support "
    "tool, not a diagnostic authority: remind the user that a qualified "
    "radiologist must verify all output when giving clinical content."
)

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_worklist",
            "description": "List studies in the local radiology worklist, optionally filtered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["unread", "in_progress", "reported"]},
                    "priority": {"type": "string", "enum": ["routine", "urgent", "stat"]},
                    "critical_only": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_study_details",
            "description": "Get details and the latest report for a specific study by id.",
            "parameters": {
                "type": "object",
                "properties": {"study_id": {"type": "integer"}},
                "required": ["study_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Search the local knowledge base of protocols and guidelines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_triage",
            "description": "Analyze report or findings text for critical/urgent findings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "modality": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_impression",
            "description": "Generate a concise numbered impression from findings text.",
            "parameters": {
                "type": "object",
                "properties": {"findings": {"type": "string"}},
                "required": ["findings"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations (all local)
# ---------------------------------------------------------------------------
def _tool_search_worklist(status: str = "", priority: str = "",
                          critical_only: bool = False, **_: Any) -> str:
    q = "SELECT id, patient_name, modality, body_part, description, study_date, " \
        "priority, status, critical FROM studies WHERE 1=1"
    params: list[Any] = []
    if status:
        q += " AND status=?"
        params.append(status)
    if priority:
        q += " AND priority=?"
        params.append(priority)
    if critical_only:
        q += " AND critical=1"
    q += " ORDER BY (priority='stat') DESC, (priority='urgent') DESC, id DESC LIMIT 25"
    conn = db.connect()
    try:
        rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    finally:
        conn.close()
    if not rows:
        return "No matching studies in the worklist."
    return json.dumps(rows, default=str)


def _tool_get_study(study_id: int, **_: Any) -> str:
    conn = db.connect()
    try:
        s = conn.execute("SELECT * FROM studies WHERE id=?", (study_id,)).fetchone()
        rep = conn.execute(
            "SELECT technique, comparison, findings, impression, status FROM reports "
            "WHERE study_id=? ORDER BY id DESC LIMIT 1", (study_id,)
        ).fetchone()
    finally:
        conn.close()
    if not s:
        return f"No study with id {study_id}."
    out = {k: s[k] for k in s.keys() if k not in ("meta_json", "frames_json")}
    out["latest_report"] = dict(rep) if rep else None
    return json.dumps(out, default=str)


def _tool_search_knowledge(query: str, top_k: int = 5, **_: Any) -> str:
    try:
        from backend.services import rag_service
        hits = rag_service.search(query, top_k=top_k)
    except Exception as exc:
        return f"Knowledge base not available: {exc}"
    items = []
    for h in hits:
        d = h.model_dump() if hasattr(h, "model_dump") else dict(h)
        items.append({
            "source": d.get("doc_title"),
            "score": round(float(d.get("score", 0)), 3),
            "text": (d.get("text") or "")[:600],
        })
    return json.dumps(items, default=str) if items else "No relevant knowledge found."


def _tool_run_triage(text: str, modality: str = "", **_: Any) -> str:
    try:
        from backend.services import triage_service
        res = triage_service.analyze(text, modality=modality)
        d = res.model_dump() if hasattr(res, "model_dump") else dict(res)
        d.pop("disclaimer", None)
        return json.dumps(d, default=str)
    except Exception as exc:
        return f"Triage not available: {exc}"


def _tool_generate_impression(findings: str, **_: Any) -> str:
    try:
        from backend.services import report_service
        out = report_service.generate_impression(ImpressionRequest(findings=findings))
        return out.impression
    except Exception as exc:
        return f"Impression generation not available: {exc}"


_DISPATCH = {
    "search_worklist": _tool_search_worklist,
    "get_study_details": _tool_get_study,
    "search_knowledge": _tool_search_knowledge,
    "run_triage": _tool_run_triage,
    "generate_impression": _tool_generate_impression,
}


def _summarize(result: str, limit: int = 240) -> str:
    result = result.replace("\n", " ")
    return result if len(result) <= limit else result[:limit] + "..."


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
def run_agent(req: ChatRequest) -> ChatResponse:
    model = req.model or config.CHAT_MODEL
    messages: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM}]

    if req.study_id:
        ctx = _tool_get_study(req.study_id)
        messages.append({
            "role": "system",
            "content": f"The user is currently viewing study id {req.study_id}. "
                       f"Context: {ctx}",
        })

    for m in req.history:
        if m.role in ("user", "assistant"):
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": req.message})

    traces: list[ToolCallTrace] = []
    use_tools = req.use_tools

    for _step in range(MAX_STEPS):
        try:
            resp = client.chat(messages, model=model, tools=TOOLS if use_tools else None)
        except Exception:
            # The model may not support tool calling. Retry without tools once.
            if use_tools:
                use_tools = False
                continue
            raise

        tool_calls = resp.get("tool_calls") or []
        if not tool_calls:
            reply = resp.get("content", "").strip()
            db.log_audit("chat", {"model": model, "tools_used": [t.name for t in traces]})
            return ChatResponse(reply=reply, tool_calls=traces, model=model,
                                disclaimer=config.DISCLAIMER)

        # Append the assistant turn that requested the tools, in OpenAI format.
        messages.append({
            "role": "assistant",
            "content": resp.get("content", "") or "",
            "tool_calls": [
                {
                    "id": tc["id"] or f"call_{i}",
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                }
                for i, tc in enumerate(tool_calls)
            ],
        })

        for i, tc in enumerate(tool_calls):
            name = tc["name"]
            args = tc["arguments"] if isinstance(tc["arguments"], dict) else {}
            fn = _DISPATCH.get(name)
            result = fn(**args) if fn else f"Unknown tool: {name}"
            traces.append(ToolCallTrace(
                name=name, arguments=args, result_summary=_summarize(result)))
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"] or f"call_{i}",
                "content": result,
            })

    # Ran out of steps: ask for a final answer with no further tools.
    try:
        final = client.chat(messages, model=model)["content"].strip()
    except Exception as exc:
        final = f"(Assistant unavailable: {exc})"
    return ChatResponse(reply=final, tool_calls=traces, model=model,
                        disclaimer=config.DISCLAIMER)

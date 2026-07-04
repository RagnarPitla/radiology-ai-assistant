"""
Report drafting service for RadHarness.

Turns a radiologist's raw dictation / findings into a structured report
(Technique, Comparison, Findings, Impression) using the LOCAL model, and
generates concise impressions. Nothing leaves the machine.

This is a drafting aid only. Output is a draft that a qualified radiologist
must review and edit. config.DISCLAIMER is attached to every result.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from backend import config, db
from backend.llm import client
from backend.schemas import ImpressionOut, ImpressionRequest, ReportDraftRequest, ReportOut

_REPORT_SYSTEM = (
    "You are a reporting assistant for a board-certified radiologist. You help "
    "draft structured diagnostic imaging reports. Follow these rules strictly:\n"
    "1. Use only the information provided. Do not invent findings, measurements, "
    "comparisons, or clinical history that were not given.\n"
    "2. Never include or infer patient identifiers.\n"
    "3. Write in a professional, concise radiology reporting style.\n"
    "4. Organize into four sections: TECHNIQUE, COMPARISON, FINDINGS, IMPRESSION.\n"
    "5. The IMPRESSION is a short numbered list of the most important, actionable "
    "conclusions, ordered by clinical significance.\n"
    "6. If a section has no supporting input, write 'Not provided' (for technique "
    "or comparison) rather than fabricating content.\n"
    "Return ONLY a JSON object with keys technique, comparison, findings, "
    "impression. No prose outside the JSON."
)

_IMPRESSION_SYSTEM = (
    "You are assisting a radiologist. Given the FINDINGS of an imaging study, "
    "write a concise IMPRESSION as a numbered list of the key actionable "
    "conclusions, ordered by clinical significance. Use only what the findings "
    "support. Do not invent new findings. Return only the impression text."
)


def _coerce_text(value: Any, numbered: bool = False) -> str:
    """Normalize a model field that may be a string, list, or dict into text."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
        if numbered:
            return "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1))
        return "\n".join(items)
    if isinstance(value, dict):
        return "\n".join(f"{k}: {v}" for k, v in value.items())
    return str(value).strip()


def _study_context(study_id: Optional[int]) -> dict[str, Any]:
    if not study_id:
        return {}
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM studies WHERE id=?", (study_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def draft_report(req: ReportDraftRequest) -> ReportOut:
    ctx = _study_context(req.study_id)
    modality = req.modality or ctx.get("modality", "")
    body_part = req.body_part or ctx.get("body_part", "")

    user = (
        f"Modality: {modality or 'Not provided'}\n"
        f"Body part: {body_part or 'Not provided'}\n"
        f"Clinical indication: {req.indication or 'Not provided'}\n"
        f"Technique notes: {req.technique or 'Not provided'}\n"
        f"Comparison: {req.comparison or 'Not provided'}\n"
        f"Reporting style: {req.style}\n\n"
        f"Radiologist findings / dictation:\n{req.findings or 'Not provided'}\n"
    )

    model = req.model or config.CHAT_MODEL
    result = {"technique": "", "comparison": "", "findings": "", "impression": ""}
    try:
        raw = client.chat(
            [
                {"role": "system", "content": _REPORT_SYSTEM},
                {"role": "user", "content": user},
            ],
            model=model,
            temperature=0.2,
        )["content"]
        parsed = client.extract_json(raw)
        if isinstance(parsed, dict):
            for k in result:
                if parsed.get(k):
                    result[k] = _coerce_text(parsed[k], numbered=(k == "impression"))
        else:
            # Model did not return JSON. Keep its prose as findings.
            result["findings"] = raw.strip()
    except Exception as exc:  # never crash the endpoint
        result["findings"] = req.findings
        result["impression"] = f"(Automated drafting unavailable: {exc})"

    # Sensible fallbacks so a draft is always usable.
    if not result["technique"]:
        result["technique"] = req.technique or "Not provided"
    if not result["comparison"]:
        result["comparison"] = req.comparison or "Not provided"
    if not result["findings"]:
        result["findings"] = req.findings or "Not provided"

    db.log_audit("report_draft", {"study_id": req.study_id, "model": model})
    return ReportOut(
        study_id=req.study_id,
        technique=result["technique"],
        comparison=result["comparison"],
        findings=result["findings"],
        impression=result["impression"],
        status="draft",
        model=model,
        disclaimer=config.DISCLAIMER,
    )


def generate_impression(req: ImpressionRequest) -> ImpressionOut:
    model = req.model or config.CHAT_MODEL
    user = (
        f"Modality: {req.modality or 'Not provided'}\n"
        f"Indication: {req.indication or 'Not provided'}\n\n"
        f"FINDINGS:\n{req.findings}\n"
    )
    try:
        text = client.complete(user, system=_IMPRESSION_SYSTEM, model=model, temperature=0.2)
    except Exception as exc:
        text = f"(Impression generation unavailable: {exc})"
    db.log_audit("impression", {"model": model})
    return ImpressionOut(impression=text.strip(), model=model, disclaimer=config.DISCLAIMER)


def save_report(report: ReportOut) -> ReportOut:
    conn = db.connect()
    try:
        ts = db.now_iso()
        if report.id:
            conn.execute(
                "UPDATE reports SET study_id=?, technique=?, comparison=?, findings=?, "
                "impression=?, status=?, model=?, updated_at=? WHERE id=?",
                (report.study_id, report.technique, report.comparison, report.findings,
                 report.impression, report.status or "draft", report.model, ts, report.id),
            )
            report_id = report.id
        else:
            cur = conn.execute(
                "INSERT INTO reports (study_id, technique, comparison, findings, impression, "
                "status, model, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (report.study_id, report.technique, report.comparison, report.findings,
                 report.impression, report.status or "draft", report.model, ts, ts),
            )
            report_id = cur.lastrowid
        # Mark the study as reported when a report is saved.
        if report.study_id:
            conn.execute(
                "UPDATE studies SET status='reported' WHERE id=? AND status!='reported'",
                (report.study_id,),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    finally:
        conn.close()
    db.log_audit("report_save", {"report_id": report_id, "study_id": report.study_id})
    return _row_to_report(dict(row))


def get_latest_report(study_id: int) -> Optional[ReportOut]:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM reports WHERE study_id=? ORDER BY id DESC LIMIT 1", (study_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_report(dict(row)) if row else None


def _row_to_report(row: dict[str, Any]) -> ReportOut:
    return ReportOut(
        id=row.get("id"),
        study_id=row.get("study_id"),
        technique=row.get("technique", ""),
        comparison=row.get("comparison", ""),
        findings=row.get("findings", ""),
        impression=row.get("impression", ""),
        status=row.get("status", "draft"),
        model=row.get("model", ""),
        created_at=row.get("created_at", "") or "",
        updated_at=row.get("updated_at", "") or "",
        disclaimer=config.DISCLAIMER,
    )

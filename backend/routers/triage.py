"""Triage router for local report text analysis."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from backend import db, schemas
from backend.services import triage_service

router = APIRouter()

_PRIORITY_RANK = {"routine": 0, "urgent": 1, "stat": 2}
_LEVEL_TO_PRIORITY = {"critical": "stat", "urgent": "urgent", "routine": "routine"}


def _study_summary_from_row(row) -> schemas.StudySummary:
    return schemas.StudySummary(
        id=row["id"],
        patient_name=row["patient_name"] or "",
        patient_id=row["patient_id"] or "",
        modality=row["modality"] or "",
        body_part=row["body_part"] or "",
        description=row["description"] or "",
        study_date=row["study_date"] or "",
        num_images=row["num_images"] or 0,
        priority=row["priority"] or "routine",
        status=row["status"] or "unread",
        critical=bool(row["critical"]),
        created_at=row["created_at"] or "",
    )


def _persist_triage(study_id: int, result: schemas.TriageResult) -> None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT id, priority FROM studies WHERE id = ?", (study_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Study not found")
        conn.execute(
            """
            INSERT INTO triage_results
                (study_id, level, critical, categories_json, rationale, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                study_id,
                result.level,
                1 if result.critical else 0,
                json.dumps(result.categories),
                result.rationale,
                result.model,
                db.now_iso(),
            ),
        )
        new_priority = _LEVEL_TO_PRIORITY.get(result.level, "routine")
        current_priority = row["priority"] or "routine"
        updates: list[str] = []
        params: list[object] = []
        if result.critical:
            updates.append("critical = ?")
            params.append(1)
        if _PRIORITY_RANK.get(new_priority, 0) > _PRIORITY_RANK.get(current_priority, 0):
            updates.append("priority = ?")
            params.append(new_priority)
        if updates:
            params.append(study_id)
            conn.execute(f"UPDATE studies SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


@router.post("/analyze", response_model=schemas.TriageResult)
def analyze(req: schemas.TriageRequest) -> schemas.TriageResult:
    result = triage_service.analyze(req.text, modality=req.modality, model=req.model)
    if req.study_id is not None:
        _persist_triage(req.study_id, result)
    db.log_audit("triage", {"level": result.level, "study_id": req.study_id})
    return result


@router.get("/critical", response_model=list[schemas.StudySummary])
def critical() -> list[schemas.StudySummary]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM studies WHERE critical = 1 ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [_study_summary_from_row(row) for row in rows]
    finally:
        conn.close()

"""
Report drafting router (prefix /api/reports).

Structured report drafting and impression generation using the local model,
plus save/retrieve of drafts. All AI output carries the medical disclaimer.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.schemas import (
    ImpressionOut,
    ImpressionRequest,
    ReportDraftRequest,
    ReportOut,
)
from backend.services import report_service

router = APIRouter()


@router.post("/draft", response_model=ReportOut)
def draft_report(req: ReportDraftRequest) -> ReportOut:
    return report_service.draft_report(req)


@router.post("/impression", response_model=ImpressionOut)
def generate_impression(req: ImpressionRequest) -> ImpressionOut:
    if not req.findings.strip():
        raise HTTPException(status_code=422, detail="findings is required")
    return report_service.generate_impression(req)


@router.post("/save", response_model=ReportOut)
def save_report(report: ReportOut) -> ReportOut:
    return report_service.save_report(report)


@router.get("/{study_id}", response_model=ReportOut)
def get_report(study_id: int) -> ReportOut:
    rep = report_service.get_latest_report(study_id)
    if rep is None:
        raise HTTPException(status_code=404, detail="No report for this study")
    return rep

"""Studies, worklist, and DICOM viewer router."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from backend import config
from backend.schemas import IngestResponse, StudyDetail, StudySummary
from backend.services import dicom_service

router = APIRouter()


class IngestRequest(BaseModel):
    path: Optional[str] = None


class StudyPatch(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None


@router.post("/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest | None = None) -> IngestResponse:
    result = dicom_service.ingest_path(request.path if request else None)
    return IngestResponse(**result)


@router.post("/ingest-upload", response_model=IngestResponse)
def ingest_upload(files: list[UploadFile] = File(...)) -> IngestResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    upload_dir = config.DICOM_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    for index, upload in enumerate(files):
        filename = Path(upload.filename or f"upload-{index}.dcm").name
        if Path(filename).suffix.lower() != ".dcm":
            raise HTTPException(status_code=400, detail="Only .dcm files are supported")
        content = upload.file.read()
        digest = hashlib.sha256(content).hexdigest()[:16]
        out_path = upload_dir / f"upload-{digest}-{index}.dcm"
        out_path.write_bytes(content)
        saved.append(out_path)

    result = dicom_service.ingest_files(saved)
    return IngestResponse(**result)


@router.get("/", response_model=list[StudySummary])
def list_worklist(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    critical: Optional[bool] = None,
) -> list[StudySummary]:
    rows = dicom_service.list_studies(
        {"status": status, "priority": priority, "critical": critical}
    )
    return [StudySummary(**row) for row in rows]


@router.get("/{study_id}", response_model=StudyDetail)
def get_study(study_id: int) -> StudyDetail:
    row = dicom_service.get_study(study_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Study not found")
    return StudyDetail(**row)


@router.get("/{study_id}/frame/{index}.png")
def get_frame(study_id: int, index: int) -> Response:
    path = dicom_service.frame_path(study_id, index)
    if path is None:
        raise HTTPException(status_code=404, detail="Frame not found")
    return Response(content=path.read_bytes(), media_type="image/png")


@router.patch("/{study_id}", response_model=StudyDetail)
def patch_study(study_id: int, patch: StudyPatch) -> StudyDetail:
    row = dicom_service.update_study(
        study_id,
        status=patch.status,
        priority=patch.priority,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Study not found")
    return StudyDetail(**row)

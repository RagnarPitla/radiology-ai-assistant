"""Image import and local vision analysis router."""
from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image

from backend import config, db
from backend.schemas import AnalysisRequest, AnalysisResult, ImageUploadResponse
from backend.services import vision_service

router = APIRouter()

_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg"}


@router.post("/upload-image", response_model=ImageUploadResponse)
def upload_image(file: UploadFile = File(...)) -> ImageUploadResponse:
    filename = Path(file.filename or "image.png").name
    suffix = Path(filename).suffix.lower()
    if suffix not in _SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PNG, JPG, and JPEG files are supported")

    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    try:
        with Image.open(BytesIO(content)) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
    except Exception:
        raise HTTPException(status_code=400, detail="Uploaded file is not a readable image")

    digest = hashlib.sha256(content).hexdigest()
    anon = f"ANON-{digest[:12].upper()}"
    original_path = config.IMAGES_DIR / f"upload-{digest[:16]}{suffix}"
    original_path.write_bytes(content)

    study_uid = f"IMAGE-{digest[:16]}-{db.now_iso()}"
    created_at = db.now_iso()
    meta = {
        "source_kind": "image",
        "original_filename": filename,
        "original_path": str(original_path),
        "sha256": digest,
        "width": width,
        "height": height,
    }

    conn = db.connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO studies (
                study_uid, patient_name, patient_id, modality, body_part,
                description, study_date, num_images, priority, status,
                critical, meta_json, frames_json, created_at, source_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                study_uid,
                anon,
                anon,
                "IMAGE",
                "",
                "Imported image",
                "",
                1,
                "routine",
                "unread",
                0,
                json.dumps(meta),
                "[]",
                created_at,
                "image",
            ),
        )
        study_id = int(cur.lastrowid)
        frame_dir = config.FRAMES_DIR / str(study_id)
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_path = frame_dir / "0.png"
        rgb.save(frame_path, format="PNG")
        meta["frame_path"] = str(frame_path)
        conn.execute(
            "UPDATE studies SET frames_json=?, meta_json=? WHERE id=?",
            (json.dumps([str(frame_path)]), json.dumps(meta), study_id),
        )
        conn.commit()
    finally:
        conn.close()

    db.log_audit("image_upload", {"study_id": study_id, "filename": filename})
    return ImageUploadResponse(
        study_id=study_id,
        image_url=f"/api/analysis/image/{study_id}.png",
        width=width,
        height=height,
        message="Image imported locally.",
    )


@router.get("/image/{study_id}.png")
def get_image(study_id: int) -> Response:
    try:
        path = vision_service._image_path_for_study(study_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Image not found")
    return Response(content=path.read_bytes(), media_type="image/png")


@router.post("/run", response_model=AnalysisResult)
def run_analysis(request: AnalysisRequest) -> AnalysisResult:
    return vision_service.analyze_image(
        request.study_id,
        focus=request.focus,
        model=request.model,
    )


@router.get("/{study_id}", response_model=AnalysisResult)
def get_analysis(study_id: int) -> AnalysisResult:
    return vision_service.get_persisted_analysis(study_id)

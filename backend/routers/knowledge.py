"""Knowledge base router for local Radiology AI Assistant RAG."""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from backend import config
from backend.schemas import KBDoc, KBIngestResponse, KBSearchRequest, KBSearchResponse
from backend.services import rag_service

router = APIRouter()


class IngestPathRequest(BaseModel):
    path: str


def _unique_kb_path(filename: str) -> Path:
    safe_name = Path(filename).name
    if not safe_name:
        safe_name = "upload.txt"
    target = config.KB_DIR / safe_name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(1, 10000):
        candidate = config.KB_DIR / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise HTTPException(status_code=409, detail="Could not create a unique upload filename")


@router.post("/ingest-upload", response_model=KBIngestResponse)
def ingest_upload(files: list[UploadFile] = File(...)) -> KBIngestResponse:
    saved: list[Path] = []
    skipped = 0
    config.KB_DIR.mkdir(parents=True, exist_ok=True)
    for upload in files:
        filename = Path(upload.filename or "").name
        if Path(filename).suffix.lower() not in rag_service.SUPPORTED_EXTENSIONS:
            skipped += 1
            continue
        target = _unique_kb_path(filename)
        try:
            with target.open("wb") as out:
                shutil.copyfileobj(upload.file, out)
            saved.append(target)
        except Exception:
            skipped += 1
        finally:
            upload.file.close()

    docs, ingest_skipped = rag_service.ingest_paths(saved)
    skipped += ingest_skipped
    return KBIngestResponse(
        ingested=docs,
        message=f"Ingested {len(docs)} document(s), skipped {skipped}.",
    )


@router.post("/ingest-path", response_model=KBIngestResponse)
def ingest_path(body: IngestPathRequest) -> KBIngestResponse:
    path = Path(body.path).expanduser()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    files = rag_service.supported_files(path)
    if not files:
        return KBIngestResponse(ingested=[], message="No supported files found.")
    docs, skipped = rag_service.ingest_paths(files)
    return KBIngestResponse(
        ingested=docs,
        message=f"Ingested {len(docs)} document(s), skipped {skipped}.",
    )


@router.get("/docs", response_model=list[KBDoc])
def docs() -> list[KBDoc]:
    return rag_service.list_docs()


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: int) -> dict[str, int]:
    if not rag_service.delete_doc(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": doc_id}


@router.post("/search", response_model=KBSearchResponse)
def search(request: KBSearchRequest) -> KBSearchResponse:
    hits = rag_service.search(request.query, request.top_k)
    return KBSearchResponse(query=request.query, hits=hits)

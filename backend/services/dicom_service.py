"""DICOM ingest, worklist, and frame helpers for RadHarness."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pydicom
from PIL import Image
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue

from backend import config, db

MAX_FRAMES_PER_STUDY = 40


def ingest_path(path: str | Path | None = None) -> dict[str, Any]:
    """Ingest DICOM files from a file or directory path."""
    root = Path(path) if path else config.DICOM_DIR
    files = _discover_files(root)
    return ingest_files(files)


def ingest_files(paths: Iterable[str | Path]) -> dict[str, Any]:
    """Ingest a concrete set of DICOM file paths."""
    groups: dict[str, list[tuple[Path, Dataset]]] = {}
    attempted = 0
    parsed = 0
    failed = 0

    for item in paths:
        attempted += 1
        file_path = Path(item)
        try:
            ds = pydicom.dcmread(str(file_path), force=True)
            study_uid = _value(ds, "StudyInstanceUID")
            if not study_uid:
                failed += 1
                continue
            groups.setdefault(study_uid, []).append((file_path, ds))
            parsed += 1
        except Exception:
            failed += 1

    ingested_rows: list[dict[str, Any]] = []
    skipped_existing = 0

    conn = db.connect()
    try:
        for study_uid, items in groups.items():
            existing = conn.execute(
                "SELECT id FROM studies WHERE study_uid = ?",
                (study_uid,),
            ).fetchone()
            if existing:
                skipped_existing += 1
                continue

            first_ds = items[0][1]
            anon = _anon_token(_value(first_ds, "PatientID") or study_uid)
            metadata = _study_metadata(study_uid, anon, items)
            frame_paths = _render_study_frames(study_uid, items)
            created_at = db.now_iso()

            conn.execute(
                """
                INSERT OR IGNORE INTO studies (
                    study_uid, patient_name, patient_id, modality, body_part,
                    description, study_date, num_images, priority, status,
                    critical, meta_json, frames_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    study_uid,
                    anon,
                    anon,
                    metadata["modality"],
                    metadata["body_part"],
                    metadata["description"],
                    metadata["study_date"],
                    len(items),
                    "routine",
                    "unread",
                    0,
                    json.dumps(metadata, default=str),
                    json.dumps([str(p) for p in frame_paths]),
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM studies WHERE study_uid = ?",
                (study_uid,),
            ).fetchone()
            if row is not None:
                ingested_rows.append(_summary_from_row(row))
        conn.commit()
    finally:
        conn.close()

    detail = {
        "files_attempted": attempted,
        "files_parsed": parsed,
        "files_failed": failed,
        "studies_seen": len(groups),
        "studies_ingested": len(ingested_rows),
        "studies_skipped_existing": skipped_existing,
    }
    db.log_audit("ingest", detail)

    return {
        "ingested": len(ingested_rows),
        "studies": ingested_rows,
        "message": (
            f"Ingested {len(ingested_rows)} studies. "
            f"Parsed {parsed} files, skipped {skipped_existing} existing studies."
        ),
    }


def list_studies(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return worklist rows, optionally filtered by status, priority, or critical."""
    filters = filters or {}
    clauses: list[str] = []
    params: list[Any] = []

    if filters.get("status"):
        clauses.append("status = ?")
        params.append(filters["status"])
    if filters.get("priority"):
        clauses.append("priority = ?")
        params.append(filters["priority"])
    if filters.get("critical") is not None:
        clauses.append("critical = ?")
        params.append(1 if bool(filters["critical"]) else 0)

    query = "SELECT * FROM studies"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY critical DESC, created_at DESC, id DESC"

    conn = db.connect()
    try:
        rows = conn.execute(query, params).fetchall()
        return [_summary_from_row(row) for row in rows]
    finally:
        conn.close()


def get_study(study_id: int) -> dict[str, Any] | None:
    """Return a detailed study row by local integer id."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM studies WHERE id = ?", (study_id,)).fetchone()
        return _detail_from_row(row) if row is not None else None
    finally:
        conn.close()


def frame_path(study_id: int, index: int) -> Path | None:
    """Return the PNG path for a rendered frame, or None if unavailable."""
    if index < 0:
        return None
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT frames_json FROM studies WHERE id = ?",
            (study_id,),
        ).fetchone()
        if row is None:
            return None
        frames = _loads_list(row["frames_json"])
        if index >= len(frames):
            return None
        path = Path(frames[index])
        return path if path.exists() and path.is_file() else None
    finally:
        conn.close()


def update_study(study_id: int, **fields: Any) -> dict[str, Any] | None:
    """Update status and priority fields for a study."""
    allowed = {
        key: value
        for key, value in fields.items()
        if key in {"status", "priority"} and value is not None
    }
    if not allowed:
        return get_study(study_id)

    assignments = ", ".join(f"{key} = ?" for key in allowed)
    params = list(allowed.values()) + [study_id]
    conn = db.connect()
    try:
        conn.execute(f"UPDATE studies SET {assignments} WHERE id = ?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM studies WHERE id = ?", (study_id,)).fetchone()
        return _detail_from_row(row) if row is not None else None
    finally:
        conn.close()


def _discover_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return [item for item in path.rglob("*") if item.is_file()]
    return []


def _anon_token(source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"ANON-{digest.upper()}"


def _safe_study_dir(study_uid: str) -> Path:
    digest = hashlib.sha256(study_uid.encode("utf-8", errors="ignore")).hexdigest()[:16]
    path = config.FRAMES_DIR / digest
    path.mkdir(parents=True, exist_ok=True)
    return path


def _study_metadata(
    study_uid: str,
    anon: str,
    items: list[tuple[Path, Dataset]],
) -> dict[str, Any]:
    first = items[0][1]
    description = _value(first, "StudyDescription") or _value(first, "SeriesDescription")
    instances: list[dict[str, Any]] = []
    for file_path, ds in _ordered_items(items):
        instances.append(
            {
                "filename": file_path.name,
                "sop_instance_uid": _value(ds, "SOPInstanceUID"),
                "series_instance_uid": _value(ds, "SeriesInstanceUID"),
                "series_number": _value(ds, "SeriesNumber"),
                "instance_number": _value(ds, "InstanceNumber"),
                "rows": int(getattr(ds, "Rows", 0) or 0),
                "columns": int(getattr(ds, "Columns", 0) or 0),
                "number_of_frames": int(_value(ds, "NumberOfFrames") or 1),
                "has_pixel_data": bool(getattr(ds, "PixelData", None)),
            }
        )
    return {
        "study_uid": study_uid,
        "patient_name": anon,
        "patient_id": anon,
        "modality": _value(first, "Modality"),
        "body_part": _value(first, "BodyPartExamined"),
        "study_date": _value(first, "StudyDate"),
        "description": description,
        "study_time": _value(first, "StudyTime"),
        "series_description": _value(first, "SeriesDescription"),
        "num_instances": len(items),
        "instances": instances,
    }


def _render_study_frames(study_uid: str, items: list[tuple[Path, Dataset]]) -> list[Path]:
    frames_dir = _safe_study_dir(study_uid)
    rendered: list[Path] = []
    frame_index = 0

    for _, ds in _ordered_items(items):
        if frame_index >= MAX_FRAMES_PER_STUDY:
            break
        if not getattr(ds, "PixelData", None):
            continue
        try:
            pixel_array = ds.pixel_array
        except Exception:
            continue

        for frame in _iter_frames(pixel_array, ds):
            if frame_index >= MAX_FRAMES_PER_STUDY:
                break
            image_array = _frame_to_uint8(frame, ds)
            image = Image.fromarray(image_array)
            out_path = frames_dir / f"frame_{frame_index:04d}.png"
            image.save(out_path, format="PNG")
            rendered.append(out_path)
            frame_index += 1

    return rendered


def _iter_frames(pixel_array: np.ndarray, ds: Dataset) -> Iterable[np.ndarray]:
    arr = np.asarray(pixel_array)
    samples = int(getattr(ds, "SamplesPerPixel", 1) or 1)
    if arr.ndim == 2:
        yield arr
    elif arr.ndim == 3 and samples > 1:
        yield arr
    elif arr.ndim == 3:
        for frame in arr:
            yield frame
    elif arr.ndim == 4:
        for frame in arr:
            yield frame


def _frame_to_uint8(frame: np.ndarray, ds: Dataset) -> np.ndarray:
    arr = frame.astype(np.float32)
    photo = str(getattr(ds, "PhotometricInterpretation", "") or "").upper()
    if photo.startswith("MONOCHROME"):
        arr = _apply_rescale(arr, ds)
        arr = _window(arr, ds)
        if photo == "MONOCHROME1":
            arr = 255.0 - arr
        return arr.astype(np.uint8)
    return _min_max(arr).astype(np.uint8)


def _apply_rescale(arr: np.ndarray, ds: Dataset) -> np.ndarray:
    slope = float(_first_number(getattr(ds, "RescaleSlope", 1)) or 1)
    intercept = float(_first_number(getattr(ds, "RescaleIntercept", 0)) or 0)
    return arr * slope + intercept


def _window(arr: np.ndarray, ds: Dataset) -> np.ndarray:
    center = _first_number(getattr(ds, "WindowCenter", None))
    width = _first_number(getattr(ds, "WindowWidth", None))
    if center is None or width is None or width <= 0:
        return _min_max(arr)
    low = center - width / 2.0
    high = center + width / 2.0
    arr = np.clip(arr, low, high)
    return ((arr - low) / (high - low) * 255.0).clip(0, 255)


def _min_max(arr: np.ndarray) -> np.ndarray:
    arr = arr.astype(np.float32)
    mn = float(np.nanmin(arr)) if arr.size else 0.0
    mx = float(np.nanmax(arr)) if arr.size else 0.0
    if mx <= mn:
        return np.zeros(arr.shape, dtype=np.float32)
    return ((arr - mn) / (mx - mn) * 255.0).clip(0, 255)


def _first_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, MultiValue) or isinstance(value, (list, tuple)):
        value = value[0] if value else None
    try:
        return float(value)
    except Exception:
        return None


def _ordered_items(items: list[tuple[Path, Dataset]]) -> list[tuple[Path, Dataset]]:
    return sorted(
        items,
        key=lambda item: (
            _sort_number(_value(item[1], "SeriesNumber")),
            _sort_number(_value(item[1], "InstanceNumber")),
            _value(item[1], "SOPInstanceUID"),
            str(item[0]),
        ),
    )


def _sort_number(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _value(ds: Dataset, name: str) -> str:
    value = getattr(ds, name, "")
    if isinstance(value, MultiValue) or isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value) if value is not None else ""


def _loads_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _loads_dict(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _summary_from_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "patient_name": row["patient_name"] or "",
        "patient_id": row["patient_id"] or "",
        "modality": row["modality"] or "",
        "body_part": row["body_part"] or "",
        "description": row["description"] or "",
        "study_date": row["study_date"] or "",
        "num_images": int(row["num_images"] or 0),
        "priority": row["priority"] or "routine",
        "status": row["status"] or "unread",
        "critical": bool(row["critical"]),
        "created_at": row["created_at"] or "",
    }


def _detail_from_row(row: Any) -> dict[str, Any]:
    detail = _summary_from_row(row)
    frames = _loads_list(row["frames_json"])
    detail.update(
        {
            "study_uid": row["study_uid"] or "",
            "meta": _loads_dict(row["meta_json"]),
            "frame_count": len(frames),
        }
    )
    return detail

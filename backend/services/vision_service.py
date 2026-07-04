"""Local vision analysis service for imported radiology images."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from backend import config, db
from backend.llm import client
from backend.schemas import AnalysisFinding, AnalysisResult, BoundingBox

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_ALLOWED_SEVERITIES = {"normal", "minor", "moderate", "critical"}

_PROMPT = """
You are an expert board-certified radiologist reviewing this image.
Review the image in UTMOST DETAIL. Be systematic and cover every visible region.
Return ONLY strict JSON with this exact shape:
{
  "summary": "one concise overall summary",
  "detail": "long, structured, exhaustive narrative review covering every region, systematically",
  "findings": [
    {
      "label": "short name",
      "description": "detailed finding",
      "severity": "normal|minor|moderate|critical",
      "box": {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
    }
  ]
}
Use normalized box coordinates from 0 to 1 with origin at the top-left. x and y are
the top-left corner. w and h are width and height as fractions of the image.
Provide a box for every localizable finding. Use severity normal for normal
localizable structures if no abnormality is present.
""".strip()


def analyze_image(study_id: int, focus: str = "", model: str | None = None) -> AnalysisResult:
    """Analyze a locally stored study image with the local vision model."""
    selected_model = model or config.VISION_MODEL
    try:
        image_path = _image_path_for_study(study_id)
        width, height = _image_size(image_path)
        content = _call_vision_model(image_path, focus, selected_model)
        parsed = client.extract_json(content)
        result = _result_from_parsed(study_id, parsed, width, height, selected_model)
        _replace_findings(study_id, result.findings, selected_model)
        db.log_audit(
            "vision_analysis",
            {"study_id": study_id, "model": selected_model, "findings": len(result.findings)},
        )
        return result
    except Exception as exc:
        _replace_findings(study_id, [], selected_model)
        db.log_audit(
            "vision_analysis_error",
            {"study_id": study_id, "model": selected_model, "error": str(exc)},
        )
        width, height = _safe_dimensions(study_id)
        return AnalysisResult(
            study_id=study_id,
            summary="Vision analysis unavailable.",
            detail=f"Local vision model call failed: {exc}",
            findings=[],
            image_url=f"/api/analysis/image/{study_id}.png",
            width=width,
            height=height,
            model=selected_model,
            disclaimer=config.DISCLAIMER,
        )


def get_persisted_analysis(study_id: int) -> AnalysisResult:
    """Return the latest persisted analysis findings for a study."""
    width, height = _safe_dimensions(study_id)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM analysis_findings WHERE study_id=? ORDER BY id ASC",
            (study_id,),
        ).fetchall()
    finally:
        conn.close()

    findings: list[AnalysisFinding] = []
    model = ""
    for index, row in enumerate(rows, 1):
        model = row["model"] or model
        findings.append(
            AnalysisFinding(
                id=row["id"],
                index=index,
                label=row["label"] or "",
                description=row["description"] or "",
                severity=_severity(row["severity"]),
                box=_box_from_json(row["box_json"]),
            )
        )
    detail = "\n".join(
        f"Finding {item.index}: {item.label}. {item.description}" for item in findings
    )
    return AnalysisResult(
        study_id=study_id,
        summary=f"{len(findings)} persisted analysis findings." if findings else "",
        detail=detail,
        findings=findings,
        image_url=f"/api/analysis/image/{study_id}.png",
        width=width,
        height=height,
        model=model,
        disclaimer=config.DISCLAIMER,
    )


def _call_vision_model(image_path: Path, focus: str, model: str) -> str:
    base = _ollama_native_base()
    prompt = _PROMPT
    if focus:
        prompt += f"\nClinical focus or question: {focus.strip()}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [_image_b64(image_path)],
            }
        ],
        "stream": False,
        "format": "json",
    }
    with httpx.Client(timeout=config.REQUEST_TIMEOUT) as http:
        response = http.post(f"{base}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
    return ((data.get("message") or {}).get("content") or "").strip()


def _ollama_native_base() -> str:
    raw = config.OLLAMA_BASE_URL.rstrip("/")
    base = raw[:-3] if raw.endswith("/v1") else raw
    parsed = urlparse(base)
    host = (parsed.hostname or "").lower()
    if host not in _LOCAL_HOSTS:
        raise RuntimeError(f"Refusing non-local vision endpoint: {base}")
    return base.rstrip("/")


def _image_path_for_study(study_id: int) -> Path:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT frames_json FROM studies WHERE id=?",
            (study_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"Study {study_id} not found")
    try:
        frames = json.loads(row["frames_json"] or "[]")
    except Exception:
        frames = []
    if not frames:
        raise ValueError(f"Study {study_id} has no image frame")
    path = Path(str(frames[0]))
    if not path.exists() or not path.is_file():
        raise ValueError(f"Study {study_id} image frame is missing")
    return path


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def _safe_dimensions(study_id: int) -> tuple[int, int]:
    try:
        return _image_size(_image_path_for_study(study_id))
    except Exception:
        return 0, 0


def _image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _result_from_parsed(
    study_id: int,
    parsed: Any,
    width: int,
    height: int,
    model: str,
) -> AnalysisResult:
    if not isinstance(parsed, dict):
        return AnalysisResult(
            study_id=study_id,
            summary="Vision model did not return valid JSON.",
            detail="The local model response could not be parsed as strict JSON.",
            findings=[],
            image_url=f"/api/analysis/image/{study_id}.png",
            width=width,
            height=height,
            model=model,
            disclaimer=config.DISCLAIMER,
        )

    findings: list[AnalysisFinding] = []
    raw_findings = parsed.get("findings") if isinstance(parsed.get("findings"), list) else []
    for index, item in enumerate(raw_findings, 1):
        if not isinstance(item, dict):
            continue
        box = _normalize_box(item.get("box"), width, height)
        findings.append(
            AnalysisFinding(
                index=index,
                label=str(item.get("label") or f"Finding {index}").strip(),
                description=str(item.get("description") or "").strip(),
                severity=_severity(item.get("severity")),
                box=box,
            )
        )

    return AnalysisResult(
        study_id=study_id,
        summary=str(parsed.get("summary") or "").strip(),
        detail=str(parsed.get("detail") or "").strip(),
        findings=findings,
        image_url=f"/api/analysis/image/{study_id}.png",
        width=width,
        height=height,
        model=model,
        disclaimer=config.DISCLAIMER,
    )


def _normalize_box(raw: Any, width: int, height: int) -> BoundingBox:
    try:
        x, y, w, h, corners = _coerce_box(raw)
        if corners:
            x1, y1, x2, y2 = x, y, w, h
            x = min(x1, x2)
            y = min(y1, y2)
            w = abs(x2 - x1)
            h = abs(y2 - y1)
        values = [x, y, w, h]
        if any(abs(value) > 1.5 for value in values):
            if width <= 0 or height <= 0:
                return BoundingBox()
            x = x / width
            w = w / width
            y = y / height
            h = h / height
        if w < 0 or h < 0:
            return BoundingBox()
        x = _clamp(x)
        y = _clamp(y)
        w = min(_clamp(w), 1.0 - x)
        h = min(_clamp(h), 1.0 - y)
        return BoundingBox(x=x, y=y, w=w, h=h)
    except Exception:
        return BoundingBox()


def _coerce_box(raw: Any) -> tuple[float, float, float, float, bool]:
    if isinstance(raw, dict):
        lower = {str(key).lower(): value for key, value in raw.items()}
        if {"x1", "y1", "x2", "y2"}.issubset(lower):
            return (
                float(lower["x1"]),
                float(lower["y1"]),
                float(lower["x2"]),
                float(lower["y2"]),
                True,
            )
        return (
            float(lower.get("x", 0.0)),
            float(lower.get("y", 0.0)),
            float(lower.get("w", lower.get("width", 0.0))),
            float(lower.get("h", lower.get("height", 0.0))),
            False,
        )
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        x, y, w, h = [float(value) for value in raw[:4]]
        return x, y, w, h, True
    return 0.0, 0.0, 0.0, 0.0, False


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _severity(value: Any) -> str:
    severity = str(value or "normal").strip().lower()
    return severity if severity in _ALLOWED_SEVERITIES else "normal"


def _replace_findings(study_id: int, findings: list[AnalysisFinding], model: str) -> None:
    conn = db.connect()
    try:
        conn.execute("DELETE FROM analysis_findings WHERE study_id=?", (study_id,))
        created_at = db.now_iso()
        for finding in findings:
            conn.execute(
                """
                INSERT INTO analysis_findings (
                    study_id, label, description, severity, box_json, model, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    study_id,
                    finding.label,
                    finding.description,
                    finding.severity,
                    json.dumps(_box_dict(finding.box)),
                    model,
                    created_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _box_dict(box: BoundingBox) -> dict[str, float]:
    if hasattr(box, "model_dump"):
        return box.model_dump()
    return box.dict()


def _box_from_json(raw: str) -> BoundingBox:
    try:
        data = json.loads(raw or "{}")
        if isinstance(data, dict):
            return BoundingBox(**data)
    except Exception:
        pass
    return BoundingBox()

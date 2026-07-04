"""Local report text triage for urgent radiology findings."""
from __future__ import annotations

import re
from typing import Any

from backend import config, db
from backend.llm import client
from backend import schemas

LEVEL_RANK = {"routine": 0, "urgent": 1, "critical": 2}
VALID_LEVELS = set(LEVEL_RANK)

CRITICAL_FINDINGS: dict[str, list[str]] = {
    "intracranial hemorrhage": [
        "intracranial hemorrhage",
        "intraparenchymal hemorrhage",
        "subdural hemorrhage",
        "subdural hematoma",
        "epidural hemorrhage",
        "epidural hematoma",
        "subarachnoid hemorrhage",
        "acute hemorrhage",
        "ICH",
    ],
    "acute stroke or large vessel occlusion": [
        "large vessel occlusion",
        "LVO",
        "acute infarct",
        "acute ischemic stroke",
        "basilar artery occlusion",
        "MCA occlusion",
        "ICA occlusion",
    ],
    "mass effect or herniation": [
        "midline shift",
        "uncal herniation",
        "tonsillar herniation",
        "subfalcine herniation",
        "brain herniation",
        "severe mass effect",
    ],
    "pneumothorax": [
        "tension pneumothorax",
        "large pneumothorax",
        "mediastinal shift from pneumothorax",
    ],
    "pulmonary embolism": [
        "saddle pulmonary embolism",
        "central pulmonary embolism",
        "massive pulmonary embolism",
        "right heart strain",
    ],
    "aortic catastrophe": [
        "aortic dissection",
        "ruptured aneurysm",
        "ruptured aortic aneurysm",
        "aortic rupture",
        "transection of the aorta",
    ],
    "perforated viscus": [
        "free intraperitoneal air",
        "pneumoperitoneum",
        "bowel perforation",
        "perforated viscus",
        "perforated bowel",
    ],
    "ischemic bowel": [
        "ischemic bowel",
        "bowel ischemia",
        "mesenteric ischemia",
        "portal venous gas",
        "pneumatosis intestinalis",
    ],
    "obstetric or pelvic emergency": [
        "ectopic pregnancy",
        "ruptured ectopic",
        "ovarian torsion",
        "testicular torsion",
    ],
    "spinal emergency": [
        "cord compression",
        "cauda equina",
        "epidural abscess",
        "spinal epidural hematoma",
    ],
    "complicated appendicitis": [
        "perforated appendicitis",
        "appendicitis with perforation",
        "appendiceal abscess",
    ],
}

URGENT_FINDINGS: dict[str, list[str]] = {
    "acute fracture": [
        "acute fracture",
        "displaced fracture",
        "hip fracture",
        "femoral neck fracture",
        "vertebral compression fracture",
    ],
    "moderate pneumothorax": ["moderate pneumothorax", "small pneumothorax"],
    "pulmonary embolism": ["pulmonary embolism", "segmental embolism", "subsegmental embolism", "small PE"],
    "possible malignancy": [
        "suspicious for malignancy",
        "new mass",
        "spiculated mass",
        "metastatic disease",
    ],
    "acute infection": ["appendicitis", "diverticulitis", "acute cholecystitis", "pyelonephritis"],
    "vascular occlusion": ["DVT", "deep venous thrombosis", "arterial occlusion"],
}

_NEGATION_CUES = {
    "no",
    "not",
    "without",
    "absent",
    "negative",
    "denies",
    "deny",
    "excluded",
    "exclude",
    "resolved",
}
_NEGATION_PHRASES = (
    "negative for",
    "no evidence of",
    "no convincing",
    "no acute",
    "ruled out",
    "rule out",
    "r/o",
    "free of",
    "lack of",
)


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term.lower())
    escaped = escaped.replace(r"\ ", r"[\s\-/]+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


def _is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 120):start].lower()
    prefix = re.sub(r"[\n\r\t]+", " ", prefix)
    tokens = re.findall(r"[a-z0-9/]+", prefix)
    window = " ".join(tokens[-8:])
    if any(phrase in window for phrase in _NEGATION_PHRASES):
        return True
    recent = tokens[-6:]
    return any(token in _NEGATION_CUES for token in recent)


def _match_terms(text: str, findings: dict[str, list[str]]) -> dict[str, list[str]]:
    matches: dict[str, list[str]] = {}
    for category, terms in findings.items():
        for term in terms:
            for match in _term_pattern(term).finditer(text):
                if _is_negated(text, match.start()):
                    continue
                matches.setdefault(category, [])
                if term not in matches[category]:
                    matches[category].append(term)
                break
    return matches


def _rule_pass(text: str) -> dict[str, Any]:
    critical_matches = _match_terms(text, CRITICAL_FINDINGS)
    urgent_matches = _match_terms(text, URGENT_FINDINGS)
    categories = list(critical_matches) + [c for c in urgent_matches if c not in critical_matches]
    matched_terms: list[str] = []
    for terms in list(critical_matches.values()) + list(urgent_matches.values()):
        for term in terms:
            if term not in matched_terms:
                matched_terms.append(term)
    level = "critical" if critical_matches else "urgent" if urgent_matches else "routine"
    rationale = "No curated critical or urgent terms were detected."
    if matched_terms:
        rationale = f"Rule pass matched: {', '.join(matched_terms)}."
    return {
        "level": level,
        "categories": categories,
        "matched_terms": matched_terms,
        "rationale": rationale,
    }


def _safe_level(value: Any) -> str:
    level = str(value or "routine").strip().lower()
    return level if level in VALID_LEVELS else "routine"


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _llm_pass(text: str, modality: str, model: str) -> dict[str, Any] | None:
    system = (
        "You are a local radiology report triage classifier. Return JSON only. "
        "No prose, markdown, or explanations outside JSON. Classify only explicit, "
        "non-negated report findings. If a finding is negated, absent, ruled out, "
        "or stated as no evidence of disease, do not count it. This is not a medical device."
    )
    user = (
        "Analyze this radiology report text for urgent triage. "
        "Return exactly this JSON shape: "
        "{\"level\": \"routine|urgent|critical\", \"categories\": [], "
        "\"critical_findings\": [], \"rationale\": \"\"}. "
        "Use critical for time-sensitive life or organ threatening findings. "
        "Use urgent for important acute findings that are not immediately life threatening. "
        "Use routine when all acute findings are negated or absent.\n"
        f"Modality: {modality or 'unknown'}\nReport text:\n{text}"
    )
    response = client.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=model,
        temperature=0,
        max_tokens=350,
    )
    parsed = client.extract_json(response.get("content", ""))
    if not isinstance(parsed, dict):
        return None
    return {
        "level": _safe_level(parsed.get("level")),
        "categories": _as_str_list(parsed.get("categories")),
        "critical_findings": _as_str_list(parsed.get("critical_findings")),
        "rationale": str(parsed.get("rationale") or "").strip(),
        "model": response.get("model") or model,
    }


def _higher_level(first: str, second: str) -> str:
    return first if LEVEL_RANK[first] >= LEVEL_RANK[second] else second


def analyze(text: str, modality: str = "", model=None) -> schemas.TriageResult:
    """Analyze report or findings text and return a local triage result."""
    requested_model = str(model or config.CHAT_MODEL)
    report_text = text or ""
    rule = _rule_pass(report_text)
    llm: dict[str, Any] | None = None
    try:
        llm = _llm_pass(report_text, modality, requested_model)
    except Exception:
        llm = None

    llm_level = _safe_level(llm.get("level") if llm else "routine")
    level = _higher_level(_safe_level(rule["level"]), llm_level)
    categories: list[str] = []
    for source in (rule.get("categories", []), llm.get("categories", []) if llm else []):
        for category in source:
            category = str(category).strip()
            if category and category not in categories:
                categories.append(category)

    rationale_parts = [str(rule.get("rationale") or "").strip()]
    if llm and llm.get("rationale"):
        rationale_parts.append(f"LLM pass: {llm['rationale']}")
    elif llm is None:
        rationale_parts.append("LLM pass unavailable, using rule pass only.")
    rationale = " ".join(part for part in rationale_parts if part)

    return schemas.TriageResult(
        level=level,
        critical=level == "critical",
        categories=categories,
        rationale=rationale,
        matched_terms=rule.get("matched_terms", []),
        model=(llm.get("model") if llm else requested_model),
        disclaimer=config.DISCLAIMER,
    )

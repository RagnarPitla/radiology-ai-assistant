"""
Radiology AI Assistant configuration.

Central settings for the local radiology AI harness. Every value can be
overridden with an environment variable so the app stays portable across
Mac and PC without code changes. Defaults target a fully local setup.

PRIVACY: OFFLINE_MODE is on by default. The LLM client refuses any base URL
that is not a loopback address, guaranteeing that inference traffic stays on
the machine and no patient data leaves the device.
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (all data is local and gitignored)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("RADIOLOGY_AI_DATA_DIR", BASE_DIR / "data"))
DICOM_DIR = Path(os.getenv("RADIOLOGY_AI_DICOM_DIR", DATA_DIR / "dicom"))
KB_DIR = Path(os.getenv("RADIOLOGY_AI_KB_DIR", DATA_DIR / "kb"))
FRAMES_DIR = Path(os.getenv("RADIOLOGY_AI_FRAMES_DIR", DATA_DIR / "frames"))
IMAGES_DIR = Path(os.getenv("RADIOLOGY_AI_IMAGES_DIR", DATA_DIR / "images"))
KB_PROCESSED_DIR = Path(os.getenv("RADIOLOGY_AI_KB_PROCESSED_DIR", DATA_DIR / "kb_processed"))
AGENTS_DIR = Path(os.getenv("RADIOLOGY_AI_AGENTS_DIR", DATA_DIR / "agents"))
SKILLS_DIR = Path(os.getenv("RADIOLOGY_AI_SKILLS_DIR", DATA_DIR / "skills"))
DB_PATH = Path(os.getenv("RADIOLOGY_AI_DB_PATH", DATA_DIR / "radharness.db"))
FRONTEND_DIR = Path(os.getenv("RADIOLOGY_AI_FRONTEND_DIR", BASE_DIR / "frontend"))

for _d in (DATA_DIR, DICOM_DIR, KB_DIR, FRAMES_DIR, IMAGES_DIR,
           KB_PROCESSED_DIR, AGENTS_DIR, SKILLS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Local model runtime
# ---------------------------------------------------------------------------
# "ollama" or "foundry". Both expose an OpenAI-compatible /v1 endpoint.
LLM_RUNTIME = os.getenv("RADIOLOGY_AI_LLM_RUNTIME", "ollama").lower()

OLLAMA_BASE_URL = os.getenv("RADIOLOGY_AI_OLLAMA_BASE_URL", "http://localhost:11434/v1")
FOUNDRY_BASE_URL = os.getenv("RADIOLOGY_AI_FOUNDRY_BASE_URL", "http://localhost:5273/v1")

# Model used for reasoning-heavy work (report drafting, triage, agent).
CHAT_MODEL = os.getenv("RADIOLOGY_AI_CHAT_MODEL", "gpt-oss:20b")
# Small fast model for lightweight classification / short tasks.
FAST_MODEL = os.getenv("RADIOLOGY_AI_FAST_MODEL", "qwen2.5-coder:1.5b")
# Embedding model for RAG. If unavailable, the client falls back to a local
# deterministic hashing embedding so knowledge search still works offline.
EMBED_MODEL = os.getenv("RADIOLOGY_AI_EMBED_MODEL", "nomic-embed-text")
# Vision-language model for image analysis and bounding-box grounding.
VISION_MODEL = os.getenv("RADIOLOGY_AI_VISION_MODEL", "qwen2.5vl:7b")

# Generation defaults
DEFAULT_TEMPERATURE = float(os.getenv("RADIOLOGY_AI_TEMPERATURE", "0.2"))
REQUEST_TIMEOUT = float(os.getenv("RADIOLOGY_AI_TIMEOUT", "180"))

# ---------------------------------------------------------------------------
# Privacy / safety
# ---------------------------------------------------------------------------
# When true, the LLM client only permits loopback model endpoints.
OFFLINE_MODE = os.getenv("RADIOLOGY_AI_OFFLINE_MODE", "1") not in ("0", "false", "False")

# Server bind (loopback only by default so the app is not exposed on the LAN).
HOST = os.getenv("RADIOLOGY_AI_HOST", "127.0.0.1")
PORT = int(os.getenv("RADIOLOGY_AI_PORT", "8000"))

DISCLAIMER = (
    "Radiology AI Assistant is a local research and productivity assistant, NOT a medical "
    "device. It is not FDA or CE cleared and must not be used for primary "
    "diagnosis or autonomous clinical decisions. All AI output must be reviewed "
    "and verified by a qualified radiologist."
)

APP_NAME = "Radiology AI Assistant"
APP_VERSION = "0.1.0"


def active_base_url() -> str:
    """Return the OpenAI-compatible base URL for the selected runtime."""
    return FOUNDRY_BASE_URL if LLM_RUNTIME == "foundry" else OLLAMA_BASE_URL

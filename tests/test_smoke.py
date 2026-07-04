"""
Radiology AI Assistant smoke tests.

Runs without any network beyond the local model runtime. Tests that do not need
a live model check structure and the privacy guard. Model-dependent checks are
skipped automatically when no local model is reachable, so this file is safe to
run in CI without Ollama.

Run with pytest:      ./.venv/bin/python -m pytest tests/test_smoke.py
Or as a plain script: ./.venv/bin/python tests/test_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from backend import config
from backend.app import app
from backend.llm import client
from backend.llm.client import OfflineViolation

TC = TestClient(app)


def test_health():
    r = TC.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["app"] == config.APP_NAME
    assert body["offline_mode"] is True


def test_config_has_disclaimer():
    r = TC.get("/api/config")
    assert r.status_code == 200
    assert "not a medical device" in r.json()["disclaimer"].lower()


def test_offline_guard_blocks_remote():
    prev = config.OFFLINE_MODE
    config.OFFLINE_MODE = True
    try:
        raised = False
        try:
            client._guard("https://api.openai.com/v1")
        except OfflineViolation:
            raised = True
        assert raised, "offline guard must block remote endpoints"
        # loopback is allowed
        assert client._guard("http://127.0.0.1:11434/v1").endswith("11434/v1")
    finally:
        config.OFFLINE_MODE = prev


def test_studies_list_is_list():
    r = TC.get("/api/studies/")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_knowledge_search_shape():
    r = TC.post("/api/knowledge/search", json={"query": "contrast", "top_k": 2})
    assert r.status_code == 200
    body = r.json()
    assert "hits" in body and isinstance(body["hits"], list)


def test_frontend_served():
    r = TC.get("/")
    assert r.status_code == 200
    assert "Radiology AI Assistant" in r.text


def _run_all():
    passed = 0
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
                passed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {name}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())

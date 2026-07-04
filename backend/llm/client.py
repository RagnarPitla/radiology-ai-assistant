"""
Local LLM client for RadHarness.

Talks to a local OpenAI-compatible endpoint (Ollama or Foundry Local). Provides
chat (with optional tool calling), embeddings (with an offline fallback), and
health/model listing.

PRIVACY GUARD: when config.OFFLINE_MODE is on, only loopback endpoints are
allowed. Any attempt to point the client at a non-local host raises, so patient
data can never be sent off the machine through this client.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from backend import config

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class OfflineViolation(RuntimeError):
    pass


def _guard(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if config.OFFLINE_MODE and host not in _LOCAL_HOSTS:
        raise OfflineViolation(
            f"Offline mode is on. Refusing non-local model endpoint: {base_url!r}"
        )
    return base_url.rstrip("/")


def _base() -> str:
    return _guard(config.active_base_url())


# ---------------------------------------------------------------------------
# Health / models
# ---------------------------------------------------------------------------
def list_models() -> list[str]:
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(f"{_base()}/models")
            r.raise_for_status()
            data = r.json().get("data", [])
            return [m.get("id", "") for m in data if m.get("id")]
    except Exception:
        return []


def is_available() -> bool:
    try:
        return len(list_models()) >= 0 and _ping()
    except Exception:
        return False


def _ping() -> bool:
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{_base()}/models")
            return r.status_code < 500
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
def chat(
    messages: list[dict[str, Any]],
    model: Optional[str] = None,
    tools: Optional[list[dict[str, Any]]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> dict[str, Any]:
    """
    Call the local chat endpoint.

    Returns a normalized dict:
      {
        "content": str,
        "tool_calls": [ {"id", "name", "arguments": dict}, ... ],
        "model": str,
        "raw": <provider response>,
      }
    """
    payload: dict[str, Any] = {
        "model": model or config.CHAT_MODEL,
        "messages": messages,
        "temperature": config.DEFAULT_TEMPERATURE if temperature is None else temperature,
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    with httpx.Client(timeout=config.REQUEST_TIMEOUT) as c:
        r = c.post(f"{_base()}/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()

    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    content = msg.get("content") or ""

    tool_calls: list[dict[str, Any]] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {}) or {}
        args_raw = fn.get("arguments", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except Exception:
            args = {}
        tool_calls.append(
            {"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args}
        )

    return {
        "content": content,
        "tool_calls": tool_calls,
        "model": data.get("model", payload["model"]),
        "raw": data,
    }


def complete(prompt: str, system: str = "", model: Optional[str] = None,
             temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
    """Convenience: single-turn completion returning plain text."""
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat(messages, model=model, temperature=temperature,
                max_tokens=max_tokens)["content"]


def extract_json(text: str) -> Optional[Any]:
    """Best-effort parse of a JSON object/array embedded in model output."""
    if not text:
        return None
    text = text.strip()
    # strip code fences
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Embeddings (with deterministic offline fallback)
# ---------------------------------------------------------------------------
_EMBED_DIM = 512


def embed(texts: list[str], model: Optional[str] = None) -> list[list[float]]:
    """
    Embed a list of texts. Tries the local embedding model first; if it is not
    available, falls back to a deterministic local hashing embedding so RAG keeps
    working fully offline without any extra model download.
    """
    if not texts:
        return []
    mdl = model or config.EMBED_MODEL
    try:
        with httpx.Client(timeout=config.REQUEST_TIMEOUT) as c:
            r = c.post(f"{_base()}/embeddings", json={"model": mdl, "input": texts})
            r.raise_for_status()
            data = r.json().get("data", [])
            vecs = [d.get("embedding") for d in data]
            if vecs and all(isinstance(v, list) and v for v in vecs):
                return vecs
    except Exception:
        pass
    return [_hash_embed(t) for t in texts]


def _hash_embed(text: str, dim: int = _EMBED_DIM) -> list[float]:
    """Deterministic bag-of-words hashing embedding. Pure local, no model."""
    vec = [0.0] * dim
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    for tok in tokens:
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def using_fallback_embeddings() -> bool:
    """True when the dedicated embed model is not reachable."""
    try:
        with httpx.Client(timeout=8) as c:
            r = c.post(
                f"{_base()}/embeddings",
                json={"model": config.EMBED_MODEL, "input": ["ping"]},
            )
            return r.status_code >= 400
    except Exception:
        return True

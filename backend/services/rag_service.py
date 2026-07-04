"""
Local RAG service for Radiology AI Assistant knowledge search.

Embeddings use backend.llm.client.embed. If config.EMBED_MODEL is not installed,
the client uses a lower quality local fallback embedding that remains fully
offline. client.using_fallback_embeddings() reports the current embedding mode.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

import numpy as np
from pypdf import PdfReader

from backend import db
from backend.llm import client
from backend.schemas import KBDoc, KBHit

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150


def _is_supported(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def supported_files(path: str | Path) -> list[Path]:
    root = Path(path).expanduser()
    if root.is_file():
        return [root] if _is_supported(root) else []
    if root.is_dir():
        return sorted(p for p in root.rglob("*") if _is_supported(p))
    return []


def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext in {".txt", ".md"}:
            return path.read_text(encoding="utf-8", errors="ignore")
        if ext == ".pdf":
            reader = PdfReader(str(path))
            parts = []
            for page in reader.pages:
                try:
                    parts.append(page.extract_text() or "")
                except Exception:
                    continue
            return "\n\n".join(parts)
    except Exception:
        return ""
    return ""


def _title_from_text(text: str, filename: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", clean)
        if heading:
            return heading.group(1).strip()[:200] or filename
        if len(clean) <= 120 and not clean.endswith("."):
            return clean[:200]
        break
    return filename


def _sentences(text: str) -> list[str]:
    parts: list[str] = []
    for paragraph in re.split(r"\n\s*\n+", text):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if not paragraph:
            continue
        parts.extend(
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", paragraph)
            if s.strip()
        )
    return parts


def _split_long_piece(piece: str, size: int = CHUNK_SIZE) -> list[str]:
    if len(piece) <= size:
        return [piece]
    return [piece[i : i + size] for i in range(0, len(piece), size)]


def chunk_text(text: str) -> list[str]:
    normalized = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n")).strip()
    if not normalized:
        return []

    pieces: list[str] = []
    for sentence in _sentences(normalized):
        pieces.extend(_split_long_piece(sentence))
    if not pieces:
        pieces = _split_long_piece(normalized)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip() if current else piece
        if len(candidate) <= CHUNK_SIZE:
            current = candidate
            continue
        if current:
            chunks.append(current)
            overlap = current[-CHUNK_OVERLAP:] if len(current) > CHUNK_OVERLAP else current
            current = f"{overlap} {piece}".strip()
            if len(current) > CHUNK_SIZE:
                chunks.extend(_split_long_piece(current))
                current = ""
        else:
            chunks.extend(_split_long_piece(piece))
    if current:
        chunks.append(current)
    return [c.strip() for c in chunks if c.strip()]


def _doc_from_row(row) -> KBDoc:
    return KBDoc(
        id=int(row["id"]),
        filename=row["filename"] or "",
        title=row["title"] or "",
        num_chunks=int(row["num_chunks"] or 0),
        created_at=row["created_at"] or "",
    )


def ingest_file(path: str | Path) -> KBDoc | None:
    source = Path(path).expanduser()
    if not _is_supported(source):
        return None

    text = _extract_text(source).strip()
    chunks = chunk_text(text)
    if not chunks:
        db.log_audit("kb_ingest", {"docs": 0, "chunks": 0, "skipped": 1})
        return None

    vectors = client.embed(chunks)
    if len(vectors) != len(chunks):
        db.log_audit("kb_ingest", {"docs": 0, "chunks": 0, "skipped": 1})
        return None

    conn = db.connect()
    try:
        created_at = db.now_iso()
        title = _title_from_text(text, source.name)
        cur = conn.execute(
            "INSERT INTO kb_docs (filename, title, num_chunks, created_at) VALUES (?, ?, ?, ?)",
            (source.name, title, len(chunks), created_at),
        )
        doc_id = int(cur.lastrowid)
        conn.executemany(
            """
            INSERT INTO kb_chunks (doc_id, chunk_index, text, embedding)
            VALUES (?, ?, ?, ?)
            """,
            [
                (doc_id, index, chunk, json.dumps(vector))
                for index, (chunk, vector) in enumerate(zip(chunks, vectors))
            ],
        )
        conn.commit()
        row = conn.execute("SELECT * FROM kb_docs WHERE id = ?", (doc_id,)).fetchone()
    finally:
        conn.close()

    db.log_audit("kb_ingest", {"docs": 1, "chunks": len(chunks), "skipped": 0})
    return _doc_from_row(row) if row else None


def ingest_paths(paths: Iterable[str | Path]) -> tuple[list[KBDoc], int]:
    docs: list[KBDoc] = []
    skipped = 0
    for path in paths:
        doc = ingest_file(path)
        if doc is None:
            skipped += 1
        else:
            docs.append(doc)
    return docs, skipped


def list_docs() -> list[KBDoc]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, filename, title, num_chunks, created_at FROM kb_docs ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [_doc_from_row(row) for row in rows]
    finally:
        conn.close()


def delete_doc(doc_id: int) -> bool:
    conn = db.connect()
    try:
        cur = conn.execute("DELETE FROM kb_docs WHERE id = ?", (doc_id,))
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
        conn.close()
    if deleted:
        db.log_audit("kb_delete", {"docs": 1, "chunks": "cascade"})
    return deleted


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0 or a.shape != b.shape:
        return 0.0
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def search(query: str, top_k: int = 5) -> list[KBHit]:
    query = (query or "").strip()
    if not query:
        return []
    top_k = max(1, int(top_k or 5))

    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT c.doc_id, d.title AS doc_title, c.chunk_index, c.text, c.embedding
            FROM kb_chunks c
            JOIN kb_docs d ON d.id = c.doc_id
            """
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []

    query_vecs = client.embed([query])
    if not query_vecs:
        return []
    query_vec = np.asarray(query_vecs[0], dtype=float)

    hits: list[KBHit] = []
    for row in rows:
        try:
            chunk_vec = np.asarray(json.loads(row["embedding"] or "[]"), dtype=float)
        except Exception:
            continue
        score = _cosine(query_vec, chunk_vec)
        hits.append(
            KBHit(
                doc_id=int(row["doc_id"]),
                doc_title=row["doc_title"] or "",
                chunk_index=int(row["chunk_index"] or 0),
                text=row["text"] or "",
                score=score,
            )
        )
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:top_k]

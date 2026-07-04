"""
Local RAG service for Radiology AI Assistant knowledge search.

Embeddings use backend.llm.client.embed. If config.EMBED_MODEL is not installed,
the client uses a lower quality local fallback embedding that remains fully
offline. client.using_fallback_embeddings() reports the current embedding mode.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import httpx
import numpy as np
from pypdf import PdfReader

from backend import config, db
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


def _safe_text(text: str) -> str:
    return (text or "").replace("\u2014", "-").replace("\u2013", "-")


def _slugify(value: str, fallback: str = "knowledge") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:80].strip("-") or fallback


def _save_processed_markdown(doc_id: int, title: str, text: str) -> Path:
    config.KB_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(f"{doc_id}-{title}", f"doc-{doc_id}")
    path = config.KB_PROCESSED_DIR / f"{slug}.md"
    content = f"# {_safe_text(title)}\n\n{_safe_text(text).strip()}\n"
    path.write_text(content, encoding="utf-8")
    return path


def _post_process_doc(doc_id: int, title: str, text: str) -> None:
    try:
        _save_processed_markdown(doc_id, title, text)
    except Exception as exc:
        db.log_audit("kb_processed_error", {"doc_id": doc_id, "error": type(exc).__name__})
    try:
        from backend.services import skills_service
        skills_service.generate_for_doc(doc_id, title, text)
    except Exception as exc:
        db.log_audit("kb_skill_error", {"doc_id": doc_id, "error": type(exc).__name__})


def _store_doc_text(
    filename: str,
    title: str,
    text: str,
    source_type: str = "file",
    source_ref: str = "",
) -> KBDoc | None:
    text = _safe_text(text).strip()
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
        cur = conn.execute(
            """
            INSERT INTO kb_docs (filename, title, num_chunks, created_at, source_type, source_ref)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (filename, title, len(chunks), created_at, source_type, source_ref),
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

    db.log_audit("kb_ingest", {"docs": 1, "chunks": len(chunks), "skipped": 0, "source_type": source_type})
    if row:
        _post_process_doc(doc_id, title, text)
        return _doc_from_row(row)
    return None


def ingest_file(path: str | Path) -> KBDoc | None:
    source = Path(path).expanduser()
    if not _is_supported(source):
        return None
    text = _extract_text(source).strip()
    title = _title_from_text(text, source.name)
    return _store_doc_text(source.name, title, text, "file", str(source))


def ingest_paths(paths: Iterable[str | Path]) -> tuple[list[KBDoc], int]:
    docs: list[KBDoc] = []
    skipped = 0
    for path in paths:
        try:
            doc = ingest_file(path)
        except Exception as exc:
            db.log_audit("kb_ingest_error", {"path": str(path), "error": type(exc).__name__})
            doc = None
        if doc is None:
            skipped += 1
        else:
            docs.append(doc)
    return docs, skipped


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = False
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self.skip = True
        if tag == "title":
            self.in_title = True
        if tag in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self.skip = False
        if tag == "title":
            self.in_title = False
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        clean = re.sub(r"\s+", " ", data).strip()
        if not clean:
            return
        if self.in_title:
            self.title = f"{self.title} {clean}".strip()
        self.parts.append(clean)
        self.parts.append(" ")

    def text(self) -> str:
        return re.sub(r"\n\s*\n+", "\n\n", "".join(self.parts)).strip()


def _html_to_text(html: str) -> tuple[str, str]:
    parser = _ReadableHTMLParser()
    parser.feed(html or "")
    return _safe_text(parser.title), _safe_text(parser.text())


def _url_filename(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or "url"
    path = parsed.path.strip("/").replace("/", "-") or "index"
    return f"{_slugify(host)}-{_slugify(path)}.md"


def _upsert_url(url: str, title: str, status: str, doc_id: int | None = None) -> None:
    now = db.now_iso()
    conn = db.connect()
    try:
        conn.execute(
            """
            INSERT INTO kb_urls (url, title, status, doc_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title=excluded.title,
                status=excluded.status,
                doc_id=excluded.doc_id
            """,
            (url, title, status, doc_id, now),
        )
        conn.commit()
    finally:
        conn.close()


def _existing_url_doc_id(url: str) -> int | None:
    conn = db.connect()
    try:
        row = conn.execute("SELECT doc_id FROM kb_urls WHERE url = ?", (url,)).fetchone()
        return int(row["doc_id"]) if row and row["doc_id"] else None
    finally:
        conn.close()


def ingest_url(url: str) -> KBDoc | None:
    clean_url = (url or "").strip()
    if not clean_url:
        return None
    old_doc_id = _existing_url_doc_id(clean_url)
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as http:
            response = http.get(clean_url, headers={"User-Agent": "Radiology-AI-Assistant/0.1"})
            response.raise_for_status()
        ctype = response.headers.get("content-type", "").lower()
        if "html" in ctype or "<html" in response.text[:500].lower():
            page_title, text = _html_to_text(response.text)
        else:
            page_title, text = "", response.text
        title = page_title or _title_from_text(text, clean_url)
        if old_doc_id:
            delete_doc(old_doc_id)
        doc = _store_doc_text(_url_filename(clean_url), title, text, "url", clean_url)
        _upsert_url(clean_url, title, "indexed" if doc else "error", doc.id if doc else None)
        return doc
    except Exception as exc:
        _upsert_url(clean_url, clean_url, "error", old_doc_id)
        db.log_audit("kb_url_error", {"url": clean_url, "error": type(exc).__name__})
        return None


def ingest_urls(urls: Iterable[str]) -> tuple[list[KBDoc], int]:
    docs: list[KBDoc] = []
    skipped = 0
    for url in urls:
        doc = ingest_url(url)
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
    try:
        from backend.services import skills_service
        skills_service.delete_for_doc(doc_id)
    except Exception:
        pass
    conn = db.connect()
    try:
        cur = conn.execute("DELETE FROM kb_docs WHERE id = ?", (doc_id,))
        conn.execute("UPDATE kb_urls SET doc_id = NULL WHERE doc_id = ?", (doc_id,))
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
        conn.close()
    if deleted:
        db.log_audit("kb_delete", {"docs": 1, "chunks": "cascade"})
    return deleted


def list_urls():
    from backend.schemas import KBUrl
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, url, title, status, created_at FROM kb_urls ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [KBUrl(id=int(r["id"]), url=r["url"] or "", title=r["title"] or "", status=r["status"] or "pending", created_at=r["created_at"] or "") for r in rows]
    finally:
        conn.close()


def delete_url(url_id: int) -> bool:
    conn = db.connect()
    try:
        row = conn.execute("SELECT doc_id FROM kb_urls WHERE id = ?", (url_id,)).fetchone()
        if not row:
            return False
        doc_id = int(row["doc_id"]) if row["doc_id"] else None
        conn.execute("DELETE FROM kb_urls WHERE id = ?", (url_id,))
        conn.commit()
    finally:
        conn.close()
    if doc_id:
        delete_doc(doc_id)
    db.log_audit("kb_url_delete", {"url_id": url_id, "doc_deleted": bool(doc_id)})
    return True


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

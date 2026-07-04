"""Generated skill and agent files for knowledge items."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend import config, db
from backend.llm import client
from backend.schemas import GeneratedSkill
from backend.services.rag_service import search


def _safe_text(text: str) -> str:
    return (text or "").replace("\u2014", "-").replace("\u2013", "-").strip()


def _slugify(value: str, fallback: str = "knowledge") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:80].strip("-") or fallback


def _skill_from_row(row) -> GeneratedSkill:
    return GeneratedSkill(
        id=int(row["id"]),
        name=row["name"] or "",
        slug=row["slug"] or "",
        description=row["description"] or "",
        source_doc_id=int(row["source_doc_id"]) if row["source_doc_id"] else None,
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
    )


def _fallback_payload(title: str, text: str) -> dict[str, Any]:
    lines = [line.strip("# ").strip() for line in _safe_text(text).splitlines() if line.strip()]
    points = lines[:5] or [_safe_text(title) or "Knowledge item"]
    return {
        "name": _safe_text(title) or "Knowledge item",
        "description": f"Local skill generated from {_safe_text(title) or 'a knowledge item'}.",
        "when_to_use": "Use when a question relates to this local knowledge item.",
        "key_points": points,
    }


def _llm_payload(title: str, text: str) -> dict[str, Any]:
    fallback = _fallback_payload(title, text)
    prompt = f"""
Create a concise skill summary for a local radiology knowledge item.
Return only JSON with keys: name, description, when_to_use, key_points.
key_points must be a list of short strings. Do not use em dashes.

Title: {title}

Knowledge text:
{text[:6000]}
"""
    try:
        raw = client.complete(
            prompt,
            system="You write concise local clinical workflow skill summaries. Do not use em dashes.",
            model=config.CHAT_MODEL,
            temperature=0.1,
            max_tokens=700,
        )
        parsed = client.extract_json(raw) if hasattr(client, "extract_json") else None
        if isinstance(parsed, dict):
            fallback.update({k: parsed.get(k) for k in fallback.keys() if parsed.get(k)})
    except Exception:
        pass
    fallback["name"] = _safe_text(str(fallback.get("name") or title))[:120]
    fallback["description"] = _safe_text(str(fallback.get("description") or ""))[:500]
    fallback["when_to_use"] = _safe_text(str(fallback.get("when_to_use") or ""))[:800]
    points = fallback.get("key_points") or []
    if not isinstance(points, list):
        points = [str(points)]
    fallback["key_points"] = [_safe_text(str(p)) for p in points if _safe_text(str(p))][:8]
    return fallback


def _markdown(payload: dict[str, Any], slug: str, doc_id: int, title: str) -> str:
    points = payload.get("key_points") or []
    point_lines = "\n".join(f"- {_safe_text(str(point))}" for point in points) or "- Review the source knowledge item."
    return (
        f"# {_safe_text(payload.get('name') or title)}\n\n"
        f"## name\n{_safe_text(payload.get('name') or title)}\n\n"
        f"## description\n{_safe_text(payload.get('description') or '')}\n\n"
        f"## when to use\n{_safe_text(payload.get('when_to_use') or '')}\n\n"
        f"## key points\n{point_lines}\n\n"
        f"## source\nKnowledge doc id: {doc_id}\nSkill slug: {slug}\n"
    )


def _agent_definition(payload: dict[str, Any], slug: str, doc_id: int) -> dict[str, Any]:
    return {
        "name": _safe_text(payload.get("name") or slug),
        "description": _safe_text(payload.get("description") or ""),
        "instructions": (
            "Use this skill for questions related to its source knowledge item. "
            "Call search_knowledge for grounding before answering. Keep all data local. "
            "Do not invent clinical guidance beyond the source."
        ),
        "skill": f"{slug}.md",
        "source_doc_id": doc_id,
        "tools": ["search_knowledge"],
    }


def _upsert_skill(name: str, slug: str, description: str, doc_id: int, skill_path: Path, agent_path: Path) -> GeneratedSkill:
    now = db.now_iso()
    conn = db.connect()
    try:
        conn.execute(
            """
            INSERT INTO generated_skills
                (name, slug, description, source_doc_id, skill_path, agent_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                source_doc_id=excluded.source_doc_id,
                skill_path=excluded.skill_path,
                agent_path=excluded.agent_path,
                updated_at=excluded.updated_at
            """,
            (name, slug, description, doc_id, str(skill_path), str(agent_path), now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM generated_skills WHERE slug = ?", (slug,)).fetchone()
        return _skill_from_row(row)
    finally:
        conn.close()


def generate_for_doc(doc_id: int, title: str, text: str) -> GeneratedSkill:
    config.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    config.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    clean_title = _safe_text(title) or f"Knowledge doc {doc_id}"
    slug = _slugify(f"{doc_id}-{clean_title}", f"doc-{doc_id}")
    payload = _llm_payload(clean_title, text)
    skill_path = config.SKILLS_DIR / f"{slug}.md"
    agent_path = config.AGENTS_DIR / f"{slug}.json"
    skill_path.write_text(_markdown(payload, slug, doc_id, clean_title), encoding="utf-8")
    agent = _agent_definition(payload, slug, doc_id)
    agent_path.write_text(json.dumps(agent, indent=2, ensure_ascii=False), encoding="utf-8")
    skill = _upsert_skill(agent["name"], slug, agent["description"], doc_id, skill_path, agent_path)
    db.log_audit("skill_generate", {"doc_id": doc_id, "slug": slug})
    return skill


def list_skills() -> list[GeneratedSkill]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM generated_skills ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [_skill_from_row(row) for row in rows]
    finally:
        conn.close()


def get_skill(slug: str) -> dict[str, Any]:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM generated_skills WHERE slug = ?", (slug,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise KeyError(slug)
    skill_path = Path(row["skill_path"] or config.SKILLS_DIR / f"{slug}.md")
    agent_path = Path(row["agent_path"] or config.AGENTS_DIR / f"{slug}.json")
    markdown = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
    agent = json.loads(agent_path.read_text(encoding="utf-8")) if agent_path.exists() else {}
    return {
        "name": row["name"] or "",
        "description": row["description"] or "",
        "markdown": _safe_text(markdown),
        "agent": agent,
    }


def delete_for_doc(doc_id: int) -> None:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT skill_path, agent_path FROM generated_skills WHERE source_doc_id = ?", (doc_id,)).fetchall()
        conn.execute("DELETE FROM generated_skills WHERE source_doc_id = ?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    for row in rows:
        for key in ("skill_path", "agent_path"):
            path = Path(row[key] or "")
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass


def _doc_text(doc_id: int) -> str:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT text FROM kb_chunks WHERE doc_id = ? ORDER BY chunk_index ASC",
            (doc_id,),
        ).fetchall()
        return "\n\n".join(row["text"] or "" for row in rows)
    finally:
        conn.close()


def regenerate_all() -> int:
    conn = db.connect()
    try:
        rows = conn.execute("SELECT id, title FROM kb_docs ORDER BY id ASC").fetchall()
    finally:
        conn.close()
    count = 0
    for row in rows:
        doc_id = int(row["id"])
        text = _doc_text(doc_id)
        if not text.strip():
            continue
        generate_for_doc(doc_id, row["title"] or f"Knowledge doc {doc_id}", text)
        count += 1
    return count


__all__ = [
    "generate_for_doc",
    "list_skills",
    "get_skill",
    "delete_for_doc",
    "regenerate_all",
    "search",
]

"""
Local SQLite persistence for Radiology AI Assistant.

All state lives in a single local database file (config.DB_PATH). Nothing is
sent anywhere. This module owns the schema and a few shared helpers; routers
and services run their own queries via connect().
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from backend import config


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS studies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    study_uid       TEXT UNIQUE,
    patient_name    TEXT DEFAULT '',
    patient_id      TEXT DEFAULT '',
    modality        TEXT DEFAULT '',
    body_part       TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    study_date      TEXT DEFAULT '',
    num_images      INTEGER DEFAULT 0,
    priority        TEXT DEFAULT 'routine',
    status          TEXT DEFAULT 'unread',
    critical        INTEGER DEFAULT 0,
    meta_json       TEXT DEFAULT '{}',
    frames_json     TEXT DEFAULT '[]',
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id        INTEGER,
    technique       TEXT DEFAULT '',
    comparison      TEXT DEFAULT '',
    findings        TEXT DEFAULT '',
    impression      TEXT DEFAULT '',
    status          TEXT DEFAULT 'draft',
    model           TEXT DEFAULT '',
    created_at      TEXT,
    updated_at      TEXT,
    FOREIGN KEY (study_id) REFERENCES studies(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS kb_docs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT,
    title           TEXT,
    num_chunks      INTEGER DEFAULT 0,
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          INTEGER,
    chunk_index     INTEGER,
    text            TEXT,
    embedding       TEXT,
    FOREIGN KEY (doc_id) REFERENCES kb_docs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS triage_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id        INTEGER,
    level           TEXT,
    critical        INTEGER DEFAULT 0,
    categories_json TEXT DEFAULT '[]',
    rationale       TEXT DEFAULT '',
    model           TEXT DEFAULT '',
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT,
    action          TEXT,
    detail          TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS analysis_findings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id        INTEGER,
    label           TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    severity        TEXT DEFAULT 'normal',
    box_json        TEXT DEFAULT '[]',
    model           TEXT DEFAULT '',
    created_at      TEXT,
    FOREIGN KEY (study_id) REFERENCES studies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS kb_urls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT UNIQUE,
    title           TEXT DEFAULT '',
    status          TEXT DEFAULT 'pending',
    doc_id          INTEGER,
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS generated_skills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT,
    slug            TEXT UNIQUE,
    description     TEXT DEFAULT '',
    source_doc_id   INTEGER,
    skill_path      TEXT DEFAULT '',
    agent_path      TEXT DEFAULT '',
    created_at      TEXT,
    updated_at      TEXT
);
"""

# Additive columns for existing installs. Each is applied best effort.
_MIGRATIONS = [
    "ALTER TABLE kb_docs ADD COLUMN source_type TEXT DEFAULT 'file'",
    "ALTER TABLE kb_docs ADD COLUMN source_ref TEXT DEFAULT ''",
    "ALTER TABLE studies ADD COLUMN source_kind TEXT DEFAULT 'dicom'",
]


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except Exception:
                pass  # column already exists
        conn.commit()
    finally:
        conn.close()


def log_audit(action: str, detail: Any = "") -> None:
    """Append a local audit entry. Never raises into request handling."""
    if not isinstance(detail, str):
        try:
            detail = json.dumps(detail, default=str)
        except Exception:
            detail = str(detail)
    try:
        conn = connect()
        conn.execute(
            "INSERT INTO audit (ts, action, detail) VALUES (?, ?, ?)",
            (now_iso(), action, detail),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}

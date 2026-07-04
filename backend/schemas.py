"""
Pydantic schemas shared across Radiology AI Assistant routers.

These are the API contracts. Routers and services must conform to these
shapes so the frontend and the agent tool layer stay stable.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    app: str
    version: str
    llm_available: bool
    runtime: str
    offline_mode: bool


class ConfigOut(BaseModel):
    app: str
    version: str
    runtime: str
    chat_model: str
    fast_model: str
    embed_model: str
    offline_mode: bool
    disclaimer: str
    models_available: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Studies / worklist
# ---------------------------------------------------------------------------
class StudySummary(BaseModel):
    id: int
    patient_name: str
    patient_id: str
    modality: str
    body_part: str
    description: str
    study_date: str
    num_images: int
    priority: str          # routine | urgent | stat
    status: str            # unread | in_progress | reported
    critical: bool
    created_at: str


class StudyDetail(StudySummary):
    study_uid: str
    meta: dict[str, Any] = Field(default_factory=dict)
    frame_count: int = 0


class IngestResponse(BaseModel):
    ingested: int
    studies: list[StudySummary] = Field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class ReportDraftRequest(BaseModel):
    study_id: Optional[int] = None
    modality: str = ""
    body_part: str = ""
    indication: str = ""
    findings: str = ""            # radiologist dictation / raw observations
    comparison: str = ""
    technique: str = ""
    style: str = "concise"        # concise | detailed
    model: Optional[str] = None


class ReportOut(BaseModel):
    id: Optional[int] = None
    study_id: Optional[int] = None
    technique: str = ""
    comparison: str = ""
    findings: str = ""
    impression: str = ""
    status: str = "draft"
    model: str = ""
    created_at: str = ""
    updated_at: str = ""
    disclaimer: str = ""


class ImpressionRequest(BaseModel):
    findings: str
    indication: str = ""
    modality: str = ""
    model: Optional[str] = None


class ImpressionOut(BaseModel):
    impression: str
    model: str = ""
    disclaimer: str = ""


# ---------------------------------------------------------------------------
# Knowledge base / RAG
# ---------------------------------------------------------------------------
class KBDoc(BaseModel):
    id: int
    filename: str
    title: str
    num_chunks: int
    created_at: str


class KBIngestResponse(BaseModel):
    ingested: list[KBDoc] = Field(default_factory=list)
    message: str = ""


class KBSearchRequest(BaseModel):
    query: str
    top_k: int = 5


class KBHit(BaseModel):
    doc_id: int
    doc_title: str
    chunk_index: int
    text: str
    score: float


class KBSearchResponse(BaseModel):
    query: str
    hits: list[KBHit] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------
class TriageRequest(BaseModel):
    text: str                       # report or findings text to analyze
    study_id: Optional[int] = None
    modality: str = ""
    model: Optional[str] = None


class TriageResult(BaseModel):
    level: str                      # routine | urgent | critical
    critical: bool
    categories: list[str] = Field(default_factory=list)
    rationale: str = ""
    matched_terms: list[str] = Field(default_factory=list)
    model: str = ""
    disclaimer: str = ""


# ---------------------------------------------------------------------------
# Chat / agent
# ---------------------------------------------------------------------------
class ChatMessage(BaseModel):
    role: str                       # user | assistant | system | tool
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    study_id: Optional[int] = None
    use_tools: bool = True
    model: Optional[str] = None


class ToolCallTrace(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    model: str = ""
    disclaimer: str = ""

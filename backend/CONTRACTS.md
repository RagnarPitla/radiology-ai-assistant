# Radiology AI Assistant: Build Contracts (read before editing)

This file is the single source of truth for module boundaries. Radiology AI Assistant is a
**local-first AI harness for radiologists**. Everything runs on the machine; no
patient data leaves the device. Inspired conceptually by RapidAI (triage),
Siemens Intelligent Imaging (workflow), and RamSoft (PACS/reporting).

## Golden rules
1. **Local only.** No cloud SDKs, no outbound HTTP except the local model runtime
   via `backend.llm.client`. Do not add telemetry.
2. **Not a medical device.** Include `config.DISCLAIMER` on any endpoint that
   returns AI-generated clinical text (reports, impression, triage, chat).
3. **No em dashes** in any code, comment, string, or doc. Use commas/colons/periods.
4. Keep endpoints synchronous `def` functions (FastAPI runs them in a threadpool).
   The LLM client is synchronous.
5. Only edit the files your workstream owns (below). Do not modify config.py,
   db.py, schemas.py, llm/client.py, or app.py.

## How to run / import
- Package root is the repo dir `radiology-harness/`. Modules import as
  `from backend import config, db`, `from backend.llm import client`,
  `from backend.schemas import ...`.
- Run the server from the repo root with the venv:
  `./.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000`
- A venv exists at `radiology-harness/.venv` with requirements installed.

## Shared modules (DO NOT EDIT)

### backend/config.py
Key values: `LLM_RUNTIME`, `CHAT_MODEL` (gpt-oss:20b), `FAST_MODEL`
(qwen2.5-coder:1.5b), `EMBED_MODEL`, `DICOM_DIR`, `KB_DIR`, `FRAMES_DIR`,
`DB_PATH`, `OFFLINE_MODE`, `DISCLAIMER`, `active_base_url()`.

### backend/db.py
- `connect()` -> sqlite3.Connection (row_factory=Row). Close it when done.
- `init_db()` creates all tables (already called on startup).
- `now_iso()`, `log_audit(action, detail)`, `row_to_dict(row)`.
- Tables: `studies, reports, kb_docs, kb_chunks, triage_results, audit`
  (full columns in db.py SCHEMA). Use these; do not create new tables unless
  your workstream needs one and it is additive.

### backend/llm/client.py
- `chat(messages, model=None, tools=None, temperature=None, max_tokens=None)`
  returns `{"content": str, "tool_calls": [{"id","name","arguments":dict}],
  "model": str, "raw": ...}`.
- `complete(prompt, system="", model=None, ...)` -> str.
- `extract_json(text)` -> parsed JSON or None (handles code fences).
- `embed(texts) -> list[list[float]]` (falls back to local hashing embedding
  offline, so it always returns vectors).
- `list_models()`, `is_available()`, `using_fallback_embeddings()`.

### backend/schemas.py
Contains all Pydantic request/response models. Reuse them. Names:
`StudySummary, StudyDetail, IngestResponse, ReportDraftRequest, ReportOut,
ImpressionRequest, ImpressionOut, KBDoc, KBIngestResponse, KBSearchRequest,
KBHit, KBSearchResponse, TriageRequest, TriageResult, ChatMessage, ChatRequest,
ToolCallTrace, ChatResponse`.

## Workstream ownership + required endpoints
All routers already exist as stubs exposing `router = APIRouter()`. Replace the
stub with the real implementation. Prefixes are applied in app.py.

### dicom-studies  (owns backend/services/dicom_service.py, backend/routers/studies.py)
Prefix `/api/studies`. Endpoints:
- `POST /ingest`            body {path?: str}  ingest DICOM files/dirs (default DICOM_DIR). -> IngestResponse
- `POST /ingest-upload`     multipart file(s) .dcm -> IngestResponse
- `GET  /`                  list worklist (optional ?status=&priority=&critical=) -> list[StudySummary]
- `GET  /{study_id}`        -> StudyDetail
- `GET  /{study_id}/frame/{index}.png`  render a DICOM frame as PNG (windowed) -> image/png
- `PATCH /{study_id}`       body {status?,priority?}  update worklist item -> StudyDetail
dicom_service: parse with pydicom, DE-IDENTIFY names/IDs (hash or "ANON-xxxx"),
extract modality/body part/date/description, count images, render frames to
FRAMES_DIR with proper windowing (numpy + Pillow), store frame paths in
studies.frames_json and metadata in meta_json.

### rag-knowledge  (owns backend/services/rag_service.py, backend/routers/knowledge.py)
Prefix `/api/knowledge`. Endpoints:
- `POST /ingest-upload`  multipart file(s) .txt/.md/.pdf -> KBIngestResponse
- `POST /ingest-path`    body {path: str} ingest a local file/dir -> KBIngestResponse
- `GET  /docs`           list docs -> list[KBDoc]
- `DELETE /docs/{doc_id}` remove a doc + its chunks
- `POST /search`         body KBSearchRequest -> KBSearchResponse
rag_service: extract text (pypdf for pdf), chunk (~800 chars w/ overlap), embed
via client.embed, store chunks+embeddings (JSON) in kb_chunks, cosine search.
Expose a reusable `search(query, top_k) -> list[KBHit]` for the agent to call.

### triage  (owns backend/services/triage_service.py, backend/routers/triage.py)
Prefix `/api/triage`. Endpoints:
- `POST /analyze`   body TriageRequest -> TriageResult (rule terms + LLM classify;
   if study_id given, persist to triage_results and update studies.priority/critical)
- `GET  /critical`  list studies currently flagged critical -> list[StudySummary]
triage_service: maintain a curated list of critical radiology findings
(e.g. intracranial hemorrhage, large vessel occlusion, pneumothorax, pulmonary
embolism, free air, aortic dissection, etc.), combine keyword match with an LLM
classifier that returns JSON {level, categories, rationale}. Expose reusable
`analyze(text, modality="") -> TriageResult`.

### reports-chat  (owns report_service.py, agent.py, routers/reports.py, routers/chat.py)
Handled by the lead. Report drafting + agentic chat with tools that call the
other services' reusable functions.

### frontend  (owns frontend/index.html, app.js, styles.css)
Single-page app served at `/`. Panels: worklist (from /api/studies), DICOM
viewer (frame PNGs), structured report editor (/api/reports), assistant chat
(/api/chat), knowledge manager (/api/knowledge), triage badges. Must show a
persistent OFFLINE/LOCAL indicator (from /api/health) and the disclaimer
(from /api/config). Vanilla HTML/CSS/JS, no build step, no external CDNs
(offline). Fetch same-origin.

## Testing your module
Boot the app and curl your endpoints, for example:
`./.venv/bin/python -m uvicorn backend.app:app --port 8000 &`
`curl -s localhost:8000/api/health`
Then exercise your routes. Ollama is running locally with gpt-oss:20b.

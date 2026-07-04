# RadHarness by Rbuild.ai

> An **Rbuild.ai** build. We design and ship private, on-device AI systems.
> Contact: **ragnar@rbuild.ai**

**A local-first AI harness for radiologists.** Everything runs on your own
machine (Mac or PC) using local language models. No patient data ever leaves the
device. No cloud. No telemetry.

RadHarness reimagines the *ideas* behind cloud radiology platforms as a private,
on-device assistant:

| Concept from | RadHarness (local) capability |
| --- | --- |
| RapidAI (triage) | On-device critical-findings triage that flags STAT studies |
| Siemens Intelligent Imaging (workflow) | Agentic worklist prioritization and workflow help |
| RamSoft (PACS/RIS) | Local DICOM worklist, basic viewer, and structured reporting |

> ## Important medical disclaimer
> RadHarness is a **research and productivity assistant, NOT a medical device.**
> It is **not FDA or CE cleared**. It must **not** be used for primary diagnosis
> or autonomous clinical decisions. Every AI-generated output must be reviewed
> and verified by a qualified radiologist. You are responsible for compliance
> with local regulations (HIPAA, GDPR, and institutional policy).

## Why local
- **Privacy by architecture.** Inference runs against a loopback model endpoint.
  Offline mode refuses any non-local endpoint, so PHI cannot be transmitted.
- **Ownership.** Studies, reports, and the knowledge base live in a local SQLite
  database and local files under `data/` (gitignored).
- **Portability.** Pure Python backend plus a no-build static frontend. Runs the
  same on macOS and Windows.

## What it does
- **Worklist + DICOM ingest**: drop `.dcm` files, auto de-identify, build a
  worklist, and view frames in the browser.
- **Structured reporting**: draft Technique / Comparison / Findings / Impression
  from your dictation, or generate an Impression from Findings.
- **Critical-findings triage**: detect actionable findings in report text and
  raise study priority to STAT.
- **Local knowledge base (RAG)**: ingest your protocols and guidelines, then get
  grounded answers with citations.
- **Agentic assistant**: a chat agent that can search the worklist, the
  knowledge base, and run triage using local tools.

## Requirements
- Python 3.10+ (tested on 3.14).
- A local model runtime, either:
  - **Ollama** (recommended). Install from https://ollama.com, then:
    ```
    ollama pull gpt-oss:20b          # reasoning model (default)
    ollama pull nomic-embed-text     # embeddings for RAG (optional but better)
    ```
  - **Foundry Local** (Microsoft). Set `RADHARNESS_LLM_RUNTIME=foundry` and
    `RADHARNESS_FOUNDRY_BASE_URL` to its OpenAI-compatible endpoint.

If no embedding model is present, RadHarness falls back to a local deterministic
embedding so knowledge search still works fully offline.

## Run
macOS / Linux:
```
./run.sh
```
Windows:
```
run.bat
```
Then open http://127.0.0.1:8000

## Configuration (environment variables)
| Variable | Default | Purpose |
| --- | --- | --- |
| `RADHARNESS_LLM_RUNTIME` | `ollama` | `ollama` or `foundry` |
| `RADHARNESS_CHAT_MODEL` | `gpt-oss:20b` | reasoning model |
| `RADHARNESS_FAST_MODEL` | `qwen2.5-coder:1.5b` | small/fast model |
| `RADHARNESS_EMBED_MODEL` | `nomic-embed-text` | embeddings for RAG |
| `RADHARNESS_OFFLINE_MODE` | `1` | refuse non-local model endpoints |
| `RADHARNESS_HOST` / `RADHARNESS_PORT` | `127.0.0.1` / `8000` | server bind |

## Architecture
```
backend/   FastAPI app, config, SQLite, local LLM client
  routers/   studies, reports, knowledge, triage, chat
  services/  dicom, rag, triage, report, agent
frontend/  static SPA (worklist, viewer, report editor, chat, KB)
data/      local-only storage (gitignored): DICOM, db, frames, knowledge
```

## Privacy notes
- Bind stays on `127.0.0.1` by default. Do not expose it to a network without
  adding authentication and encryption appropriate to your environment.
- The `data/` directory can contain PHI. It is gitignored. Handle and back it up
  according to your institution's policy.

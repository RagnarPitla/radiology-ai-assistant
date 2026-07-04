"""
RadHarness FastAPI application.

Local-first AI harness for radiologists. Serves the API and the static SPA.
Binds to loopback by default. No cloud calls.

Run:  uvicorn backend.app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import config, db
from backend.llm import client
from backend.schemas import ConfigOut, HealthOut

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)

# Loopback-only origins. The app is meant to run on the radiologist's machine.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://127.0.0.1:{config.PORT}",
        f"http://localhost:{config.PORT}",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    db.log_audit("startup", {"version": config.APP_VERSION, "runtime": config.LLM_RUNTIME})


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(
        status="ok",
        app=config.APP_NAME,
        version=config.APP_VERSION,
        llm_available=client.is_available(),
        runtime=config.LLM_RUNTIME,
        offline_mode=config.OFFLINE_MODE,
    )


@app.get("/api/config", response_model=ConfigOut)
def get_config() -> ConfigOut:
    return ConfigOut(
        app=config.APP_NAME,
        version=config.APP_VERSION,
        runtime=config.LLM_RUNTIME,
        chat_model=config.CHAT_MODEL,
        fast_model=config.FAST_MODEL,
        embed_model=config.EMBED_MODEL,
        offline_mode=config.OFFLINE_MODE,
        disclaimer=config.DISCLAIMER,
        models_available=client.list_models(),
    )


# ---------------------------------------------------------------------------
# Feature routers. Each router module exposes `router = APIRouter()`.
# Imports are defensive so the app still boots if a module is mid-development.
# ---------------------------------------------------------------------------
def _include(module_path: str, prefix: str, tag: str) -> None:
    try:
        mod = __import__(module_path, fromlist=["router"])
        app.include_router(mod.router, prefix=prefix, tags=[tag])
    except Exception as exc:  # pragma: no cover - dev resilience
        @app.get(f"{prefix}/_unavailable")
        def _unavailable(_e=str(exc)):
            return JSONResponse(
                status_code=503,
                content={"error": f"{tag} router unavailable", "detail": _e},
            )


_include("backend.routers.studies", "/api/studies", "studies")
_include("backend.routers.reports", "/api/reports", "reports")
_include("backend.routers.knowledge", "/api/knowledge", "knowledge")
_include("backend.routers.triage", "/api/triage", "triage")
_include("backend.routers.chat", "/api/chat", "chat")


# ---------------------------------------------------------------------------
# Frontend (served last so /api takes precedence)
# ---------------------------------------------------------------------------
if config.FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="app")


@app.get("/")
def index():
    idx = config.FRONTEND_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return JSONResponse({"app": config.APP_NAME, "docs": "/docs", "health": "/api/health"})

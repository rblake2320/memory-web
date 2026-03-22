"""
MemoryWeb FastAPI application — port 8100.
"""

import asyncio
import logging
import logging.handlers
import time as _time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .config import settings
from .database import ensure_schema_and_extensions, engine
from .models import Base
from .routers import ingest, memory, retention, search, status, chat as chat_router, settings_router
from .workers import embedding_worker

# ── Logging: console + rotating file ──────────────────────────────────────────
_log_dir = Path(__file__).parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s -- %(message)s")  # ASCII only

_file_handler = logging.handlers.RotatingFileHandler(
    _log_dir / "memoryweb.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)

_root = logging.getLogger()
_root.setLevel(logging.INFO)
_root.addHandler(_file_handler)
_root.addHandler(_console_handler)

logger = logging.getLogger(__name__)


async def _watchdog_embedding_worker():
    """
    Phase 1f: Asyncio background task that monitors EmbeddingWorker heartbeat.
    If no heartbeat for >5 minutes, automatically restarts the worker.
    Checks every 60 seconds.
    """
    STALE_THRESHOLD = embedding_worker.HEARTBEAT_STALE_SECS
    CHECK_INTERVAL = 60  # seconds

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            w = embedding_worker._worker
            if w is None:
                logger.warning("EmbeddingWorker is None — restarting")
                embedding_worker.start_worker()
            elif not w.is_alive():
                logger.warning("EmbeddingWorker thread is dead — restarting")
                embedding_worker.start_worker()
            else:
                age = _time.time() - w.last_heartbeat
                if age > STALE_THRESHOLD:
                    logger.warning(
                        "EmbeddingWorker stale (last heartbeat %.0fs ago, threshold=%ds) — restarting",
                        age, STALE_THRESHOLD,
                    )
                    embedding_worker.stop_worker()
                    embedding_worker.start_worker()
        except Exception as e:
            logger.error("EmbeddingWorker watchdog error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure schema, extensions, tables, and background workers."""
    logger.info("MemoryWeb starting on port %d", settings.MW_PORT)
    try:
        ensure_schema_and_extensions()
        Base.metadata.create_all(bind=engine)
        logger.info("Database schema ready")
    except Exception as e:
        logger.error("DB startup failed: %s", e)

    # Start background embedding worker
    embedding_worker.start_worker()
    logger.info("Embedding worker started")

    # Phase 1f: Start embedding worker heartbeat watchdog
    asyncio.create_task(_watchdog_embedding_worker())
    logger.info("Embedding worker watchdog started (check_interval=60s, stale_threshold=%ds)",
                embedding_worker.HEARTBEAT_STALE_SECS)

    # Preload sentence-transformers model so Tier 3 first query is fast
    try:
        from .services import retrieval as _retrieval
        _retrieval.warmup_model()
        logger.info("Sentence-transformers model warmed up (%s)", settings.MW_EMBED_MODEL)
    except Exception as e:
        logger.warning("Model warmup failed (Tier 3 first query will be slow): %s", e)

    yield

    # Graceful shutdown
    embedding_worker.stop_worker()
    logger.info("MemoryWeb shutting down")


app = FastAPI(
    title="MemoryWeb",
    description="Provenance-Aware Tiered Memory System",
    version="0.1.0",
    lifespan=lifespan,
)

_cors_origins = (
    settings.MW_CORS_ORIGINS.split(",")
    if settings.MW_CORS_ORIGINS and settings.MW_CORS_ORIGINS != "*"
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # credentials=True is incompatible with wildcard origins per the CORS spec
    allow_credentials=(_cors_origins != ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)


# Optional API key auth — only active when MW_API_KEY is set
_NO_AUTH_PATHS = {"/api/health", "/api/status", "/"}

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if settings.MW_API_KEY and request.url.path.startswith("/api/"):
        if request.url.path not in _NO_AUTH_PATHS:
            key = (
                request.headers.get("X-API-Key")
                or request.query_params.get("api_key")
            )
            if key != settings.MW_API_KEY:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key. Set X-API-Key header."},
                )
    return await call_next(request)

# Register routers
app.include_router(status.router)
app.include_router(ingest.router)
app.include_router(search.router)
app.include_router(memory.router)
app.include_router(retention.router)
app.include_router(chat_router.router)
app.include_router(settings_router.router)


@app.get("/", tags=["root"])
def root():
    dashboard = Path(__file__).parent.parent / "static" / "dashboard.html"
    if dashboard.exists():
        return FileResponse(str(dashboard), media_type="text/html")
    return {"service": "MemoryWeb", "version": "0.1.0", "docs": "/docs"}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.MW_PORT,
        reload=False,
        log_level="info",
    )

"""Health + stats status endpoint."""

import logging

from fastapi import APIRouter
from sqlalchemy import text

from ..database import engine, db_session
from ..models import Conversation, Embedding, Memory, Message, PipelineRun, Segment, Source
from ..schemas import PipelineHealthOut, ServiceStatus, StatsOut, StatusResponse
from ..services.ollama_client import is_available as ollama_available

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["status"])


def _check_postgres() -> ServiceStatus:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return ServiceStatus(name="postgres", healthy=True)
    except Exception as e:
        return ServiceStatus(name="postgres", healthy=False, detail=str(e))


def _check_pgvector() -> ServiceStatus:
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            ).fetchone()
        healthy = result is not None
        return ServiceStatus(name="pgvector", healthy=healthy, detail=None if healthy else "extension not installed")
    except Exception as e:
        return ServiceStatus(name="pgvector", healthy=False, detail=str(e))


def _check_redis() -> ServiceStatus:
    try:
        import redis
        from ..config import settings
        r = redis.from_url(settings.MW_REDIS_URL, socket_connect_timeout=2)
        r.ping()
        return ServiceStatus(name="redis", healthy=True)
    except Exception as e:
        return ServiceStatus(name="redis", healthy=False, detail=str(e))


def _check_ollama() -> ServiceStatus:
    healthy = ollama_available()
    return ServiceStatus(name="ollama", healthy=healthy, detail=None if healthy else "not reachable")


def _check_celery() -> ServiceStatus:
    try:
        from ..celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=2.0)
        workers = inspect.ping()
        healthy = bool(workers)
        return ServiceStatus(
            name="celery",
            healthy=healthy,
            detail=f"{len(workers)} workers" if workers else "no workers",
        )
    except Exception as e:
        return ServiceStatus(name="celery", healthy=False, detail=str(e))


def _get_stats() -> StatsOut:
    with db_session() as db:
        return StatsOut(
            sources=db.query(Source).count(),
            conversations=db.query(Conversation).count(),
            messages=db.query(Message).filter(Message.tombstoned_at.is_(None)).count(),
            segments=db.query(Segment).filter(Segment.tombstoned_at.is_(None)).count(),
            memories=db.query(Memory).filter(Memory.tombstoned_at.is_(None)).count(),
            embeddings=db.query(Embedding).count(),
            tombstoned_memories=db.query(Memory).filter(Memory.tombstoned_at.isnot(None)).count(),
        )


def _get_pipeline_health() -> PipelineHealthOut:
    with db_session() as db:
        counts = {
            status: db.query(PipelineRun).filter(PipelineRun.status == status).count()
            for status in ("done", "pending", "running", "failed")
        }
    total = sum(counts.values())
    return PipelineHealthOut(
        done=counts["done"],
        pending=counts["pending"],
        running=counts["running"],
        failed=counts["failed"],
        total=total,
    )


@router.post("/maintain/rebuild-embeddings")
def rebuild_embeddings():
    """
    Rebuild all embeddings after pgvector is installed.
    Runs alembic migration 002 to upgrade column type, then re-embeds all segments + memories.
    """
    from ..models import _HAS_PGVECTOR
    if not _HAS_PGVECTOR:
        return {"ok": False, "detail": "pgvector not installed. Run: CREATE EXTENSION vector; then restart server."}
    try:
        import subprocess, sys, os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=project_root,
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return {"ok": False, "detail": f"alembic failed: {result.stderr}"}
    except Exception as e:
        return {"ok": False, "detail": f"alembic error: {e}"}

    from ..pipelines.embedder import embed_memories, embed_segments
    from ..models import Conversation
    with db_session() as db:
        conv_ids = [r.id for r in db.query(Conversation.id).all()]

    seg_count = 0
    for cid in conv_ids:
        try:
            seg_count += embed_segments(cid)
        except Exception:
            pass
    mem_count = embed_memories()
    return {"ok": True, "segments_embedded": seg_count, "memories_embedded": mem_count}


@router.get("/health")
def get_health():
    """Minimal health check — returns 200 if the app is running."""
    return {"status": "ok", "version": "0.1.0"}


@router.get("/status", response_model=StatusResponse)
def get_status():
    """Health check for all services + DB statistics."""
    services = [
        _check_postgres(),
        _check_pgvector(),
        _check_redis(),
        _check_ollama(),
        _check_celery(),
    ]

    try:
        stats = _get_stats()
    except Exception as e:
        logger.error("Stats query failed: %s", e)
        stats = StatsOut(
            sources=0, conversations=0, messages=0,
            segments=0, memories=0, embeddings=0, tombstoned_memories=0,
        )

    try:
        pipeline_health = _get_pipeline_health()
    except Exception as e:
        logger.error("Pipeline health query failed: %s", e)
        pipeline_health = None

    return StatusResponse(services=services, stats=stats, pipeline_health=pipeline_health)

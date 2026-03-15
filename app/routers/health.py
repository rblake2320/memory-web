"""Health check endpoint."""
import logging
import requests
from fastapi import APIRouter
from sqlalchemy import text
from ..database import engine
from ..celery_app import celery_app
from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])

def _check_postgres() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as e:
        logger.error(f"Postgres health check failed: {e}")
        return "error"

def _check_celery() -> str:
    try:
        inspect = celery_app.control.inspect(timeout=2.0)
        workers = inspect.ping()
        if workers:
            return "ok"
        else:
            return "error"
    except Exception as e:
        logger.error(f"Celery health check failed: {e}")
        return "error"

def _check_ollama() -> str:
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=2)
        if response.status_code == 200:
            return "ok"
        else:
            return "error"
    except Exception as e:
        logger.error(f"Ollama health check failed: {e}")
        return "error"

def _get_embedding_queue_depth() -> int:
    try:
        from ..tasks.pipeline_tasks import embedding_task
        return embedding_task.queue.qsize()
    except Exception as e:
        logger.error(f"Embedding queue depth check failed: {e}")
        return 0

@router.get("/health")
async def get_health():
    postgres_status = _check_postgres()
    celery_status = _check_celery()
    ollama_status = _check_ollama()
    embedding_queue_depth = _get_embedding_queue_depth()
    
    if all(status == "ok" for status in [postgres_status, celery_status, ollama_status]):
        status = "ok"
    else:
        status = "error"
    
    return {
        "status": status,
        "postgres": postgres_status,
        "celery": celery_status,
        "ollama": ollama_status,
        "embedding_queue_depth": embedding_queue_depth,
    }

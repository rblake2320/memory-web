"""
Celery tasks for data ingestion.
"""

import logging
from typing import Any, Dict, Optional

from ..celery_app import celery_app
from ..services import ingestion

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="memoryweb.ingest_session")
def ingest_session_task(self, path: str, force: bool = False) -> Dict[str, Any]:
    """Ingest a single Claude session JSONL file."""
    self.update_state(state="STARTED", meta={"path": path, "stage": "ingesting"})
    try:
        result = ingestion.ingest_session_file(path, force=force)
        return result
    except Exception as e:
        logger.error("ingest_session_task failed: %s", e)
        self.update_state(state="FAILURE", meta={"error": str(e)})
        raise


@celery_app.task(bind=True, name="memoryweb.ingest_all_sessions")
def ingest_all_sessions_task(
    self, directory: Optional[str] = None, force: bool = False
) -> Dict[str, Any]:
    """Ingest all .jsonl session files in directory."""
    self.update_state(state="STARTED", meta={"stage": "scanning"})
    try:
        result = ingestion.ingest_all_sessions(directory, force=force)
        return result
    except Exception as e:
        logger.error("ingest_all_sessions_task failed: %s", e)
        raise


@celery_app.task(bind=True, name="memoryweb.ingest_shared_chat")
def ingest_shared_chat_task(
    self,
    directory: Optional[str] = None,
    limit: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Ingest AI Army shared chat markdown files."""
    self.update_state(state="STARTED", meta={"stage": "scanning"})
    try:
        result = ingestion.ingest_shared_chat(directory, limit=limit, force=force)
        return result
    except Exception as e:
        logger.error("ingest_shared_chat_task failed: %s", e)
        raise


@celery_app.task(bind=True, name="memoryweb.ingest_chatgpt")
def ingest_chatgpt_task(self, path: str, force: bool = False) -> Dict[str, Any]:
    """Ingest a ChatGPT data export (conversations.json or .zip)."""
    self.update_state(state="STARTED", meta={"path": path, "stage": "parsing"})
    try:
        result = ingestion.ingest_chatgpt_export(path, force=force)
        return result
    except Exception as e:
        logger.error("ingest_chatgpt_task failed: %s", e)
        self.update_state(state="FAILURE", meta={"error": str(e)})
        raise


@celery_app.task(bind=True, name="memoryweb.ingest_sqlite_memory")
def ingest_sqlite_memory_task(self, path: Optional[str] = None) -> Dict[str, Any]:
    """Import existing SQLite memory.db."""
    self.update_state(state="STARTED", meta={"stage": "importing"})
    try:
        result = ingestion.ingest_sqlite_memory(path)
        return result
    except Exception as e:
        logger.error("ingest_sqlite_memory_task failed: %s", e)
        raise

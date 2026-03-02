"""Ingest API routes."""

import logging
import shutil
import zipfile
from pathlib import Path
from typing import List, Optional

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from ..celery_app import celery_app
from ..deps import get_db
from ..models import Source
from ..schemas import (
    IngestAllSessionsRequest,
    IngestSessionRequest,
    IngestSharedChatRequest,
    IngestSqliteMemoryRequest,
    IngestStatusResponse,
    SourceOut,
    TaskResponse,
)
from ..tasks.ingest_tasks import (
    ingest_all_sessions_task,
    ingest_chatgpt_task,
    ingest_session_task,
    ingest_shared_chat_task,
    ingest_sqlite_memory_task,
)
from ..tasks.pipeline_tasks import run_full_pipeline

# Upload directory (inside container it's a volume; on bare-metal it's relative to project)
_UPLOAD_DIR = Path(__file__).parent.parent.parent / "data" / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


def _celery_unavailable(op: str) -> JSONResponse:
    msg = f"Celery/Redis unavailable — cannot queue '{op}'. Start Redis and Celery worker then retry."
    logger.error(msg)
    return JSONResponse(status_code=503, content={"detail": msg})


@router.post("/session", response_model=TaskResponse)
def ingest_session(body: IngestSessionRequest):
    """Ingest one Claude session JSONL file asynchronously."""
    try:
        task = ingest_session_task.delay(body.path, body.force)
        run_full_pipeline.apply_async(countdown=2, kwargs={"source_id": -1})
        return TaskResponse(task_id=task.id, status="queued", message=f"Ingesting {body.path}")
    except Exception:
        return _celery_unavailable("ingest_session")


@router.post("/session/all", response_model=TaskResponse)
def ingest_all_sessions(body: IngestAllSessionsRequest):
    """Ingest all session JSONLs in directory."""
    try:
        task = ingest_all_sessions_task.delay(body.directory, body.force)
        return TaskResponse(task_id=task.id, status="queued", message="Ingesting all sessions")
    except Exception:
        return _celery_unavailable("ingest_all_sessions")


@router.post("/shared-chat", response_model=TaskResponse)
def ingest_shared_chat(body: IngestSharedChatRequest):
    """Ingest AI Army shared chat markdown files."""
    try:
        task = ingest_shared_chat_task.delay(body.directory, body.limit, body.force)
        return TaskResponse(task_id=task.id, status="queued", message="Ingesting shared chat")
    except Exception:
        return _celery_unavailable("ingest_shared_chat")


@router.post("/sqlite-memory", response_model=TaskResponse)
def ingest_sqlite_memory(body: IngestSqliteMemoryRequest):
    """Import existing SQLite memory.db."""
    try:
        task = ingest_sqlite_memory_task.delay(body.path)
        return TaskResponse(task_id=task.id, status="queued", message="Importing SQLite memory")
    except Exception:
        return _celery_unavailable("ingest_sqlite_memory")


@router.get("/status/{task_id}", response_model=IngestStatusResponse)
def get_ingest_status(task_id: str):
    """Poll status of an async ingest task."""
    result = AsyncResult(task_id, app=celery_app)
    info = result.info or {}
    return IngestStatusResponse(
        task_id=task_id,
        status=result.status,
        stage=info.get("stage") if isinstance(info, dict) else None,
        records_processed=info.get("records_processed") if isinstance(info, dict) else None,
        error=str(info) if result.status == "FAILURE" else None,
        result=info if result.status == "SUCCESS" and isinstance(info, dict) else None,
    )


@router.get("/sources", response_model=List[SourceOut])
def list_sources(db: Session = Depends(get_db)):
    """List all ingested sources."""
    sources = db.query(Source).order_by(Source.ingested_at.desc()).all()
    return sources


@router.post("/sample")
def ingest_sample_data(db: Session = Depends(get_db)):
    """
    Load sample conversations for first-time users.
    Creates 5 realistic dev/AI conversations so you can explore the UI immediately.
    Runs synchronously and then queues the pipeline. Safe to call multiple times.
    """
    import json
    from datetime import datetime
    from pathlib import Path as _Path
    from ..models import Conversation, Message, Source
    from ..tasks.pipeline_tasks import run_full_pipeline

    sample_file = _Path(__file__).parent.parent / "data" / "sample_conversations.json"
    if not sample_file.exists():
        raise HTTPException(status_code=500, detail="Sample data file not found")

    sample_data = json.loads(sample_file.read_text(encoding="utf-8"))

    # Use a stable hash so re-runs are skipped
    import hashlib
    sample_hash = hashlib.sha256(b"memoryweb_sample_v1").hexdigest()
    existing = db.query(Source).filter(Source.source_hash == sample_hash).first()
    if existing:
        return {"source_id": existing.id, "skipped": True, "reason": "sample_already_loaded"}

    source = Source(
        source_type="sample",
        source_path="sample://built-in",
        source_hash=sample_hash,
        file_size_bytes=sample_file.stat().st_size,
        message_count=sum(len(c.get("messages", [])) for c in sample_data),
    )
    db.add(source)
    db.flush()

    total_messages = 0
    for conv_data in sample_data:
        msgs = conv_data.get("messages", [])
        conv = Conversation(
            source_id=source.id,
            external_id=conv_data.get("id", f"sample-{conv_data['title'][:20]}"),
            title=conv_data.get("title", "Sample Conversation"),
            participant="assistant",
            started_at=datetime.utcnow(),
            ended_at=datetime.utcnow(),
            message_count=len(msgs),
        )
        db.add(conv)
        db.flush()

        msg_objs = []
        for i, m in enumerate(msgs):
            msg = Message(
                conversation_id=conv.id,
                ordinal=i,
                role=m.get("role", "user"),
                content=(m.get("content", ""))[:32000],
                raw_json=m,
                char_offset_start=0,
                char_offset_end=len(m.get("content", "")),
                sent_at=datetime.utcnow(),
            )
            msg_objs.append(msg)
        db.bulk_save_objects(msg_objs)
        total_messages += len(msgs)

    db.commit()

    # Queue the processing pipeline
    try:
        run_full_pipeline.apply_async(kwargs={"source_id": source.id})
    except Exception:
        pass  # Pipeline is optional; data is already ingested

    return {
        "source_id": source.id,
        "conversations": len(sample_data),
        "messages": total_messages,
        "skipped": False,
        "message": "Sample data loaded. Processing pipeline queued — memories will appear in ~1 minute.",
    }


@router.post("/upload", response_model=TaskResponse)
async def upload_file(file: UploadFile = File(...)):
    """
    Upload a conversation export file. Supports:
    - .jsonl    — Claude Code session
    - .json     — ChatGPT conversations.json export
    - .zip      — ChatGPT data export archive (contains conversations.json)
    - .db/.sqlite — SQLite memory database

    The format is auto-detected. Returns a task ID to poll with GET /api/ingest/status/{task_id}.
    """
    suffix = Path(file.filename or "upload").suffix.lower()
    dest = _UPLOAD_DIR / file.filename

    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {e}")

    dest_str = str(dest)

    # Detect format and queue appropriate task
    try:
        if suffix == ".jsonl":
            task = ingest_session_task.delay(dest_str, False)
            run_full_pipeline.apply_async(countdown=2, kwargs={"source_id": -1})
            return TaskResponse(task_id=task.id, status="queued",
                                message=f"Ingesting Claude session: {file.filename}")

        elif suffix == ".zip":
            # ChatGPT export zip
            task = ingest_chatgpt_task.delay(dest_str, False)
            return TaskResponse(task_id=task.id, status="queued",
                                message=f"Ingesting ChatGPT export: {file.filename}")

        elif suffix == ".json":
            # Peek to detect ChatGPT format (array at root)
            is_chatgpt = False
            try:
                import json
                with dest.open() as f:
                    head = f.read(200)
                is_chatgpt = head.strip().startswith("[")
            except Exception:
                pass

            if is_chatgpt:
                task = ingest_chatgpt_task.delay(dest_str, False)
                return TaskResponse(task_id=task.id, status="queued",
                                    message=f"Ingesting ChatGPT export: {file.filename}")
            else:
                raise HTTPException(status_code=415,
                                    detail="Unknown JSON format. Expected ChatGPT conversations.json or a .jsonl Claude session.")

        elif suffix in (".db", ".sqlite"):
            task = ingest_sqlite_memory_task.delay(dest_str)
            return TaskResponse(task_id=task.id, status="queued",
                                message=f"Importing SQLite memory: {file.filename}")

        else:
            raise HTTPException(status_code=415,
                                detail=f"Unsupported file type: {suffix}. Supported: .jsonl, .json, .zip, .db")

    except HTTPException:
        raise
    except Exception:
        return _celery_unavailable("upload")


@router.post("/pipeline/{source_id}", response_model=TaskResponse)
def run_pipeline(source_id: int):
    """Manually trigger the full processing pipeline for a source."""
    try:
        task = run_full_pipeline.delay(source_id)
        return TaskResponse(
            task_id=task.id,
            status="queued",
            message=f"Pipeline started for source {source_id}",
        )
    except Exception:
        return _celery_unavailable(f"run_pipeline/{source_id}")

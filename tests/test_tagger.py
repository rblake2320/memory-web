"""Tests for the multi-axis tagger (mocked Ollama)."""

from unittest.mock import patch

import pytest

from app.pipelines.tagger import AXES, _build_tag_prompt


def test_tag_prompt_includes_all_axes():
    prompt = _build_tag_prompt("We are debugging a pgvector installation on PostgreSQL 16.")
    assert "domain" in prompt
    assert "intent" in prompt
    assert "sensitivity" in prompt
    assert "importance" in prompt
    assert "project" in prompt


def test_axes_definition():
    assert "domain" in AXES
    assert "infrastructure" in AXES["domain"]
    assert "debugging" in AXES["intent"]
    assert "public" in AXES["sensitivity"]

# Import statements needed (only new ones not in existing code)
from fastapi import APIRouter
from sqlalchemy import func
from app import crud, models, schemas
from app.database import SessionLocal

# New endpoint
router = APIRouter()

@router.get("/api/stats", response_model=schemas.Stats)
def read_stats(db: SessionLocal = Depends()):
    memory_count = db.query(models.Memory).count()
    source_count = db.query(models.Source).count()
    conversation_count = db.query(models.Conversation).count()
    embedding_count = db.query(models.Embedding).count()
    embedding_coverage_pct = db.query(func.avg(models.Memory.embedding_coverage)).scalar()
    db_size_mb = db.execute("SELECT pg_size_pretty(pg_database_size('memoryweb'))").scalar()
    return {
        "memory_count": memory_count,
        "source_count": source_count,
        "conversation_count": conversation_count,
        "embedding_count": embedding_count,
        "embedding_coverage_pct": embedding_coverage_pct,
        "db_size_mb": db_size_mb,
    }

# New schema
class Stats(BaseModel):
    memory_count: int
    source_count: int
    conversation_count: int
    embedding_count: int
    embedding_coverage_pct: float
    db_size_mb: str

# Add new endpoint to app/main.py
from app.routers import stats
app.include_router(stats.router)

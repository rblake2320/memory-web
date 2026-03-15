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

# tests/test_api.py
---NEW CODE---

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, literal
from datetime import datetime, timedelta
from app.main import app, db
from app.schemas import MemoryOut, StatsDay

client = TestClient(app, raise_server_exceptions=False)

# Additional imports for SQLAlchemy querying
from sqlalchemy import text

# New function to test daily stats endpoint
def test_daily_stats():
    # Define the date range for the last 7 days
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=7)
    
    # Construct the query to get daily stats
    query = text("""
        SELECT DATE(created_at) as date,
               COUNT(*) FILTER (WHERE type = 'memory') as memories_created,
               COUNT(*) FILTER (WHERE type = 'source') as sources_added,
               COUNT(*) as searches
        FROM memories
        JOIN sources ON memories.source_id = sources.id
        WHERE created_at >= :start_date AND created_at <= :end_date
        GROUP BY DATE(created_at)
    """)
    
    # Execute the query using SQLAlchemy
    result = db.session.execute(query, {"start_date": start_date, "end_date": end_date})
    
    # Convert the result to a list of StatsDay objects
    daily_stats = [StatsDay(date=row.date, memories_created=row.memories_created, sources_added=row.sources_added, searches=row.searches) for row in result]
    
    # Make a GET request to the /api/stats/daily endpoint
    response = client.get("/api/stats/daily")
    assert response.status_code == 200
    
    # Check the response data matches our expected daily stats
    assert response.json() == daily_stats

---END NEW CODE

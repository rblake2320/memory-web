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

from sqlalchemy import func
from app.db.models import Source
from app.schemas import MemoryStatsResponse

def test_sources_stats_endpoint():
    with httpx.Client(base_url=BASE) as client:
        response = client.get("/api/sources/stats")
        assert response.status_code == 200
        stats_response = MemoryStatsResponse(**response.json())
        assert isinstance(stats_response, MemoryStatsResponse)
        assert isinstance(stats_response.total_sources, int)
        assert isinstance(stats_response.sources_by_type, dict)
        assert isinstance(stats_response.oldest_source_date, str)
        assert isinstance(stats_response.newest_source_date, str)
        assert stats_response.sources_by_type == {
            'user': int(response.json()['sources_by_type']['user']),
            'assistant': int(response.json()['sources_by_type']['assistant']),
            # Add other agent types if present
        }
        assert stats_response.oldest_source_date == min(response.json()['sources_by_type'].keys())
        assert stats_response.newest_source_date == max(response.json()['sources_by_type'].keys())

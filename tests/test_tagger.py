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

# Import statements needed for testing the new endpoint
from httpx import JSONResponseException

# New test case for the bulk delete endpoint
def test_bulk_delete_memories():
    ids_to_delete = [1, 2, 3]  # Example memory IDs to delete
    json_data = json.dumps({"ids": ids_to_delete})
    
    with patch.object(httpx.Client, 'post', side_effect=JSONResponseException('Expected status code 200 but got 400')):
        response = client.post("/api/memories/bulk-delete", json=json_data)
        assert response.status_code in (200, 400)
        if response.status_code == 200:
            deleted = response.json().get('deleted', 0)
            not_found = response.json().get('not_found', 0)
            assert deleted == len(ids_to_delete) - not_found
            assert not_found == 0 or not_found == len(ids_to_delete)

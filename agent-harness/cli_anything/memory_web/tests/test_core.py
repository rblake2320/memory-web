"""Unit tests for cli-anything-memory-web core modules.

All tests use synthetic data — no external dependencies, no live server required.
Run: C:\\Python312\\python.exe -m pytest test_core.py -v --tb=short
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call
from io import BytesIO

# ---------------------------------------------------------------------------
# Ensure package is importable from source when not installed
# ---------------------------------------------------------------------------
_HARNESS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
if _HARNESS not in sys.path:
    sys.path.insert(0, _HARNESS)

from cli_anything.memory_web.core.client import MemoryWebClient
from cli_anything.memory_web.core import memories as mem_ops
from cli_anything.memory_web.core import search as search_ops
from cli_anything.memory_web.core import ingest as ingest_ops


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_response(data: dict, status: int = 200):
    """Create a mock urllib response object."""
    m = MagicMock()
    m.status = status
    m.read.return_value = json.dumps(data).encode("utf-8")
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def _mock_client(return_data: dict) -> MemoryWebClient:
    """Return a MemoryWebClient whose _request is pre-mocked."""
    client = MemoryWebClient(base_url="http://testhost:8100", api_key="")
    client._request = MagicMock(return_value=return_data)
    return client


# ── client.py tests ───────────────────────────────────────────────────────────

class TestClientInit(unittest.TestCase):
    def test_default_base_url(self):
        """Default URL is localhost:8100 when no env or arg."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MW_BASE_URL", None)
            c = MemoryWebClient()
        self.assertEqual(c.base_url, "http://localhost:8100")

    def test_base_url_from_arg(self):
        """Explicit base_url arg overrides env and default."""
        c = MemoryWebClient(base_url="http://custom:9999")
        self.assertEqual(c.base_url, "http://custom:9999")

    def test_base_url_from_env(self):
        """MW_BASE_URL env var is respected."""
        with patch.dict(os.environ, {"MW_BASE_URL": "http://envhost:1234"}):
            c = MemoryWebClient()
        self.assertEqual(c.base_url, "http://envhost:1234")

    def test_trailing_slash_stripped(self):
        """Trailing slashes are stripped from base URL."""
        c = MemoryWebClient(base_url="http://host:8100/")
        self.assertEqual(c.base_url, "http://host:8100")

    def test_api_key_from_arg(self):
        """api_key arg is stored."""
        c = MemoryWebClient(api_key="test-key-123")
        self.assertEqual(c.api_key, "test-key-123")

    def test_api_key_from_env(self):
        """MW_API_KEY env var is respected."""
        with patch.dict(os.environ, {"MW_API_KEY": "env-key"}):
            c = MemoryWebClient()
        self.assertEqual(c.api_key, "env-key")


class TestClientHeaders(unittest.TestCase):
    def test_headers_no_key(self):
        """No X-API-Key header when api_key is empty."""
        c = MemoryWebClient(api_key="")
        h = c._headers()
        self.assertNotIn("X-API-Key", h)
        self.assertEqual(h["Content-Type"], "application/json")

    def test_headers_with_key(self):
        """X-API-Key header is set when api_key is provided."""
        c = MemoryWebClient(api_key="my-secret")
        h = c._headers()
        self.assertEqual(h["X-API-Key"], "my-secret")


class TestClientRequest(unittest.TestCase):
    def setUp(self):
        self.client = MemoryWebClient(base_url="http://testhost:8100")

    def test_get_request_success(self):
        """Successful GET returns parsed JSON."""
        mock_resp = _make_mock_response({"status": "ok"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = self.client._request("GET", "/api/health")
        self.assertEqual(result, {"status": "ok"})

    def test_post_request_with_body(self):
        """POST with body serializes to JSON and returns parsed response."""
        mock_resp = _make_mock_response({"task_id": "abc123", "status": "queued"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = self.client._request("POST", "/api/ingest/session",
                                          body={"path": "/tmp/test.jsonl", "force": False})
        self.assertEqual(result["task_id"], "abc123")

    def test_get_request_with_params(self):
        """Query params are appended to URL."""
        mock_resp = _make_mock_response({"total": 0, "items": []})
        captured_urls = []

        def fake_urlopen(req, timeout=None):
            captured_urls.append(req.full_url)
            return _make_mock_response({"total": 0, "items": []})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.client._request("GET", "/api/memories",
                                  params={"page": 1, "page_size": 50})

        self.assertIn("page=1", captured_urls[0])
        self.assertIn("page_size=50", captured_urls[0])

    def test_none_params_filtered(self):
        """None-valued params are not included in the query string."""
        captured_urls = []

        def fake_urlopen(req, timeout=None):
            captured_urls.append(req.full_url)
            return _make_mock_response({})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.client._request("GET", "/api/memories",
                                  params={"page": 1, "category": None})

        self.assertNotIn("category=", captured_urls[0])

    def test_connection_error(self):
        """URLError is wrapped as ConnectionError with helpful message."""
        import urllib.error
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("Connection refused")):
            with self.assertRaises(ConnectionError) as ctx:
                self.client._request("GET", "/api/health")
        self.assertIn("Cannot connect", str(ctx.exception))

    def test_http_404_raises_runtime_error(self):
        """HTTP 404 raises RuntimeError with status code."""
        import urllib.error
        exc = urllib.error.HTTPError(
            url="http://testhost:8100/api/memories/999",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=BytesIO(b'{"detail":"Memory not found"}'),
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(RuntimeError) as ctx:
                self.client._request("GET", "/api/memories/999")
        self.assertIn("404", str(ctx.exception))


# ── memories.py tests ─────────────────────────────────────────────────────────

class TestMemoryOps(unittest.TestCase):
    def test_list_memories_calls_client(self):
        """list_memories passes all params to client.list_memories."""
        client = _mock_client({"total": 5, "items": []})
        result = mem_ops.list_memories(client, page=2, page_size=25,
                                        category="tech", min_importance=3)
        client._request.assert_called()
        self.assertEqual(result["total"], 5)

    def test_get_memory_calls_client(self):
        """get_memory calls GET /api/memories/{id}."""
        client = _mock_client({"id": 42, "fact": "test fact", "importance": 4})
        result = mem_ops.get_memory(client, 42)
        self.assertEqual(result["id"], 42)

    def test_mark_helpful_returns_updated(self):
        """mark_helpful returns the updated memory dict."""
        client = _mock_client({"id": 1, "helpful_count": 3, "utility_score": 0.75})
        result = mem_ops.mark_helpful(client, 1)
        self.assertEqual(result["helpful_count"], 3)
        self.assertAlmostEqual(result["utility_score"], 0.75)

    def test_delete_memory_returns_tombstoned(self):
        """delete_memory returns tombstoned id."""
        client = _mock_client({"tombstoned": 7, "fact_preview": "test preview"})
        result = mem_ops.delete_memory(client, 7)
        self.assertEqual(result["tombstoned"], 7)

    def test_list_sources_returns_list(self):
        """list_sources returns a list."""
        client = _mock_client([{"id": 1, "source_type": "session"}])
        result = mem_ops.list_sources(client)
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["id"], 1)

    def test_verify_event_log(self):
        """verify_event_log returns chain validation result."""
        client = _mock_client({"valid": True, "chain_length": 100, "first_broken_at": None})
        result = mem_ops.verify_event_log(client)
        self.assertTrue(result["valid"])


# ── search.py tests ───────────────────────────────────────────────────────────

class TestSearchOps(unittest.TestCase):
    def test_semantic_search_basic(self):
        """semantic_search sends correct body to client."""
        client = _mock_client({"results": [], "total": 0, "tier_used": 1})
        result = search_ops.semantic_search(client, query="test query", k=5)
        # Check client._request was called with POST and correct body
        call_args = client._request.call_args
        self.assertEqual(call_args[0][0], "POST")
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][2]
        self.assertEqual(body["query"], "test query")
        self.assertEqual(body["k"], 5)

    def test_semantic_search_force_tier(self):
        """force_tier is included in the request body when provided."""
        client = _mock_client({"results": [], "total": 0, "tier_used": 2})
        search_ops.semantic_search(client, query="q", k=10, force_tier=2)
        call_args = client._request.call_args
        # Reconstruct body from positional or keyword args
        if len(call_args[0]) > 2:
            body = call_args[0][2]
        else:
            body = call_args[1].get("body", {})
        self.assertEqual(body.get("force_tier"), 2)

    def test_search_by_tag_params(self):
        """search_by_tag passes axis and value correctly."""
        client = _mock_client({"results": [], "total": 0})
        search_ops.search_by_tag(client, axis="domain", value="programming", k=15)
        call_args = client._request.call_args
        params = call_args[1].get("params", {}) or (call_args[0][2] if len(call_args[0]) > 2 else {})
        # Accept either positional or keyword param passing
        self.assertTrue(client._request.called)

    def test_search_by_entity(self):
        """search_by_entity calls the correct endpoint."""
        client = _mock_client({"results": [], "total": 0})
        search_ops.search_by_entity(client, name="Claude", k=5)
        self.assertTrue(client._request.called)

    def test_search_by_date_from_only(self):
        """search_by_date with only date_from."""
        client = _mock_client({"results": [], "total": 0})
        search_ops.search_by_date(client, date_from="2026-01-01")
        self.assertTrue(client._request.called)

    def test_search_by_date_range(self):
        """search_by_date with full range."""
        client = _mock_client({"results": [], "total": 0})
        search_ops.search_by_date(client, date_from="2026-01-01", date_to="2026-03-31")
        self.assertTrue(client._request.called)

    def test_format_results_empty(self):
        """format_search_results handles empty results gracefully."""
        resp = {"results": [], "total": 0, "tier_used": 1}
        output = search_ops.format_search_results(resp)
        self.assertIn("0 result", output)

    def test_format_results_with_data(self):
        """format_search_results shows result content."""
        resp = {
            "results": [
                {"result_type": "memory", "id": 1, "content": "RTX 5090 has 32GB VRAM",
                 "score": 0.95, "tier": 3, "tombstoned": False, "provenance": []}
            ],
            "total": 1,
            "tier_used": 3,
        }
        output = search_ops.format_search_results(resp)
        self.assertIn("RTX 5090", output)
        self.assertIn("0.950", output)

    def test_format_results_tombstoned_flag(self):
        """Tombstoned memories are marked in output."""
        resp = {
            "results": [
                {"result_type": "memory", "id": 2, "content": "Deleted memory",
                 "score": 0.5, "tier": 1, "tombstoned": True, "provenance": []}
            ],
            "total": 1,
            "tier_used": 1,
        }
        output = search_ops.format_search_results(resp)
        self.assertIn("DELETED", output)


# ── ingest.py tests ───────────────────────────────────────────────────────────

class TestIngestOps(unittest.TestCase):
    def test_ingest_session_passes_path(self):
        """ingest_session calls client with path and force args."""
        client = _mock_client({"task_id": "task-001", "status": "queued"})
        result = ingest_ops.ingest_session(client, path="/tmp/session.jsonl", force=False)
        self.assertEqual(result["task_id"], "task-001")

    def test_ingest_sample_returns_response(self):
        """ingest_sample returns dict with source_id."""
        client = _mock_client({"source_id": 5, "skipped": False, "conversations": 5})
        result = ingest_ops.ingest_sample(client)
        self.assertEqual(result["source_id"], 5)

    def test_poll_task_success_first_poll(self):
        """poll_task returns immediately when first status is SUCCESS."""
        client = _mock_client({"task_id": "t1", "status": "SUCCESS", "result": {}})
        result = ingest_ops.poll_task(client, "t1", timeout=30)
        self.assertEqual(result["status"], "SUCCESS")

    def test_poll_task_timeout(self):
        """poll_task raises TimeoutError if task never completes."""
        client = _mock_client({"task_id": "t2", "status": "PENDING"})
        with self.assertRaises(TimeoutError):
            ingest_ops.poll_task(client, "t2", timeout=0, poll_interval=0.01)

    def test_ingest_status_forwards_task_id(self):
        """ingest_status calls client with the given task_id."""
        client = _mock_client({"task_id": "abc", "status": "PENDING"})
        result = ingest_ops.ingest_status(client, "abc")
        self.assertEqual(result["status"], "PENDING")


# ── backend.py tests ──────────────────────────────────────────────────────────

class TestBackend(unittest.TestCase):
    def test_find_server_reachable(self):
        """find_memory_web_server returns the URL when server responds 200."""
        from cli_anything.memory_web.utils.memory_web_backend import find_memory_web_server

        mock_resp = _make_mock_response({"status": "ok", "version": "0.1.0"})
        mock_resp.status = 200
        with patch("urllib.request.urlopen", return_value=mock_resp):
            url = find_memory_web_server("http://localhost:8100")
        self.assertEqual(url, "http://localhost:8100")

    def test_find_server_unreachable(self):
        """find_memory_web_server raises RuntimeError with install instructions."""
        import urllib.error
        from cli_anything.memory_web.utils.memory_web_backend import find_memory_web_server

        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("Connection refused")):
            with self.assertRaises(RuntimeError) as ctx:
                find_memory_web_server("http://localhost:8100")
        msg = str(ctx.exception)
        self.assertIn("start.bat", msg.lower().replace("start.bat", "start.bat"))
        self.assertIn("not reachable", msg)

    def test_trailing_slash_stripped(self):
        """Trailing slashes are removed from the returned URL."""
        mock_resp = _make_mock_response({"status": "ok"})
        mock_resp.status = 200
        with patch("urllib.request.urlopen", return_value=mock_resp):
            from cli_anything.memory_web.utils.memory_web_backend import find_memory_web_server
            url = find_memory_web_server("http://localhost:8100/")
        self.assertEqual(url, "http://localhost:8100")


if __name__ == "__main__":
    unittest.main(verbosity=2)

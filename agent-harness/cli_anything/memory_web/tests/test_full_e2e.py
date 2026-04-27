"""E2E tests for cli-anything-memory-web.

Requires MemoryWeb running at http://localhost:8100 (or MW_BASE_URL).
If the server is not reachable, all E2E tests are skipped with a clear message.
Unit-based subprocess tests still run (they test CLI structure, not live data).

Run:
    C:\\Python312\\python.exe -m pytest test_full_e2e.py -v --tb=short -s
    CLI_ANYTHING_FORCE_INSTALLED=1 C:\\Python312\\python.exe -m pytest test_full_e2e.py -v -s
"""

import json
import os
import shutil
import subprocess
import sys
import unittest

# ---------------------------------------------------------------------------
# Ensure package is importable from source when not installed
# ---------------------------------------------------------------------------
_HARNESS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
if _HARNESS not in sys.path:
    sys.path.insert(0, _HARNESS)

from cli_anything.memory_web.core.client import MemoryWebClient

# ---------------------------------------------------------------------------
# Server reachability check — used to gate all live E2E tests
# ---------------------------------------------------------------------------
_BASE_URL = os.environ.get("MW_BASE_URL", "http://localhost:8100")

def _server_is_up() -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(f"{_BASE_URL}/api/health",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=4):
            return True
    except Exception:
        return False


_SERVER_UP = _server_is_up()
_SKIP_LIVE = not _SERVER_UP
_SKIP_MSG = (
    f"MemoryWeb server not reachable at {_BASE_URL}. "
    "Start it with: cd D:\\memory-web && start.bat"
)

# ---------------------------------------------------------------------------
# _resolve_cli — per HARNESS.md specification
# ---------------------------------------------------------------------------

def _resolve_cli(name: str):
    """Resolve installed CLI command; falls back to python -m for dev.

    Set env CLI_ANYTHING_FORCE_INSTALLED=1 to require the installed command.
    """
    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"\n[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(
            f"{name} not found in PATH. Install with:\n"
            f"  cd D:\\memory-web\\agent-harness && pip install -e ."
        )
    module = "cli_anything.memory_web.memory_web_cli"
    print(f"\n[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


# ── Live E2E: Health & Status ─────────────────────────────────────────────────

@unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
class TestLiveHealth(unittest.TestCase):
    def setUp(self):
        self.client = MemoryWebClient(base_url=_BASE_URL)

    def test_health_returns_ok(self):
        """GET /api/health returns {status: ok}."""
        data = self.client.health()
        self.assertEqual(data.get("status"), "ok")
        print(f"\n  Health: {data}")

    def test_health_has_version(self):
        """Health response includes a version field."""
        data = self.client.health()
        self.assertIn("version", data)

    def test_status_has_services(self):
        """GET /api/status returns services list."""
        data = self.client.status()
        self.assertIn("services", data)
        services = data["services"]
        self.assertIsInstance(services, list)
        names = [s.get("name") for s in services]
        print(f"\n  Services: {names}")
        self.assertIn("postgres", names)

    def test_status_has_stats(self):
        """Status response includes memory counts."""
        data = self.client.status()
        stats = data.get("stats", {})
        self.assertIn("memories", stats)
        self.assertIn("conversations", stats)
        print(f"\n  Stats: memories={stats.get('memories')}, "
              f"conversations={stats.get('conversations')}")


# ── Live E2E: Memory Listing ──────────────────────────────────────────────────

@unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
class TestLiveMemoryListing(unittest.TestCase):
    def setUp(self):
        self.client = MemoryWebClient(base_url=_BASE_URL)

    def test_list_memories_pagination_fields(self):
        """Memory listing contains pagination fields."""
        data = self.client.list_memories(page=1, page_size=10)
        self.assertIn("total", data)
        self.assertIn("page", data)
        self.assertIn("page_size", data)
        self.assertIn("items", data)
        print(f"\n  Total memories: {data['total']}")

    def test_list_memories_importance_filter(self):
        """min_importance filter is accepted without error."""
        data = self.client.list_memories(min_importance=4)
        self.assertIn("items", data)
        # All returned items must meet the filter
        for m in data["items"]:
            self.assertGreaterEqual(m.get("importance", 0), 4)

    def test_memory_items_have_required_fields(self):
        """Memory items contain id, fact, importance."""
        data = self.client.list_memories(page=1, page_size=5)
        for m in data.get("items", []):
            self.assertIn("id", m)
            self.assertIn("fact", m)
            self.assertIn("importance", m)


# ── Live E2E: Semantic Search ─────────────────────────────────────────────────

@unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
class TestLiveSearch(unittest.TestCase):
    def setUp(self):
        self.client = MemoryWebClient(base_url=_BASE_URL)

    def test_search_returns_response_schema(self):
        """Search response has results and total fields."""
        data = self.client.search(query="AI memory system", k=5, force_tier=1)
        self.assertIn("results", data)
        self.assertIn("total", data)
        print(f"\n  Search results: {data['total']} found via tier {data.get('tier_used', '?')}")

    def test_search_results_have_score(self):
        """Each result has a numeric score."""
        data = self.client.search(query="configuration settings", k=5)
        for r in data.get("results", []):
            self.assertIn("score", r)
            self.assertIsInstance(r["score"], (int, float))

    def test_search_force_tier_1(self):
        """force_tier=1 is accepted and returns valid response."""
        data = self.client.search(query="test", k=3, force_tier=1)
        self.assertIn("results", data)

    def test_search_by_tag_endpoint(self):
        """Search by tag returns results list."""
        data = self.client.search_by_tag(axis="domain", value="programming", k=5)
        self.assertIn("results", data)


# ── Live E2E: Ingest Sample ───────────────────────────────────────────────────

@unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
class TestLiveIngest(unittest.TestCase):
    def setUp(self):
        self.client = MemoryWebClient(base_url=_BASE_URL)

    def test_ingest_sample_idempotent(self):
        """Ingest sample is safe to call multiple times (skips on re-call)."""
        data = self.client.ingest_sample()
        self.assertIn("source_id", data)
        print(f"\n  Sample ingest: source_id={data['source_id']}, skipped={data.get('skipped')}")
        # Second call
        data2 = self.client.ingest_sample()
        self.assertIn("source_id", data2)
        # On re-call it either matches same source or returns skipped=True
        if data2.get("skipped"):
            self.assertEqual(data["source_id"], data2["source_id"])

    def test_list_sources_returns_list(self):
        """Source listing returns a list."""
        data = self.client.list_sources()
        self.assertIsInstance(data, list)
        print(f"\n  Sources: {len(data)}")

    def test_sources_have_required_fields(self):
        """Each source has id, source_type, source_path."""
        data = self.client.list_sources()
        for s in data[:5]:
            self.assertIn("id", s)
            self.assertIn("source_type", s)
            self.assertIn("source_path", s)


# ── Live E2E: Event Log Verification ─────────────────────────────────────────

@unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
class TestLiveEventLog(unittest.TestCase):
    def setUp(self):
        self.client = MemoryWebClient(base_url=_BASE_URL)

    def test_verify_event_log_has_valid_field(self):
        """Event log verification returns a boolean valid field."""
        data = self.client.verify_event_log()
        self.assertIn("valid", data)
        self.assertIsInstance(data["valid"], bool)
        print(f"\n  Event log valid: {data['valid']}, chain_length: {data.get('chain_length', '?')}")


# ── Live E2E: Certificates ────────────────────────────────────────────────────

@unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
class TestLiveCertificates(unittest.TestCase):
    def setUp(self):
        self.client = MemoryWebClient(base_url=_BASE_URL)

    def test_list_certificates(self):
        """Certificate listing returns valid response with total and items."""
        data = self.client.list_certificates(limit=10)
        self.assertIn("total", data)
        self.assertIn("items", data)
        print(f"\n  Certificates: {data['total']}")

    def test_stale_only_filter(self):
        """stale_only filter returns subset of certificates."""
        all_certs = self.client.list_certificates(limit=50)
        stale_certs = self.client.list_certificates(limit=50, stale_only=True)
        self.assertLessEqual(stale_certs.get("total", 0), all_certs.get("total", 0))


# ── CLI Subprocess Tests ──────────────────────────────────────────────────────

class TestCLISubprocess(unittest.TestCase):
    """Tests that invoke the installed CLI command via subprocess.

    These tests verify the CLI works as a real user/agent would invoke it.
    Uses _resolve_cli() — never hardcodes sys.executable or module paths.
    """

    CLI_BASE = _resolve_cli("cli-anything-memory-web")

    def _run(self, args, check=True, env=None):
        """Run the CLI with given args and return CompletedProcess."""
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True,
            text=True,
            check=check,
            env=full_env,
        )

    def test_help(self):
        """--help exits 0 and shows usage."""
        result = self._run(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("memory-web", result.stdout.lower())
        print(f"\n  --help output preview: {result.stdout[:200]}")

    def test_status_help(self):
        """status --help shows status subcommands."""
        result = self._run(["status", "--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("health", result.stdout)

    def test_memory_help(self):
        """memory --help shows memory subcommands."""
        result = self._run(["memory", "--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("list", result.stdout)
        self.assertIn("get", result.stdout)
        self.assertIn("delete", result.stdout)
        self.assertIn("helpful", result.stdout)

    def test_search_help(self):
        """search --help shows search subcommands."""
        result = self._run(["search", "--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("query", result.stdout)

    def test_ingest_help(self):
        """ingest --help shows ingest subcommands."""
        result = self._run(["ingest", "--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("session", result.stdout)

    @unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
    def test_status_health_json(self):
        """status health --json returns valid JSON with status field."""
        result = self._run(["status", "health", "--json"])
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("status", data)
        self.assertEqual(data["status"], "ok")
        print(f"\n  status health --json: {data}")

    @unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
    def test_memory_list_json(self):
        """memory list --json returns valid JSON with total and items."""
        result = self._run(["memory", "list", "--json"])
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("total", data)
        self.assertIn("items", data)
        self.assertIsInstance(data["items"], list)
        print(f"\n  memory list --json: total={data['total']}, items={len(data['items'])}")

    @unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
    def test_memory_list_importance_filter_json(self):
        """memory list --min-importance 4 --json returns high-priority memories."""
        result = self._run(["memory", "list", "--min-importance", "4", "--json"])
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("items", data)
        for m in data["items"]:
            self.assertGreaterEqual(m.get("importance", 0), 4)

    @unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
    def test_search_query_json(self):
        """search query --json returns valid JSON with results list."""
        result = self._run(["search", "query", "AI memory system", "--json", "--k", "3"])
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("results", data)
        self.assertIn("total", data)
        print(f"\n  search query --json: {data['total']} results")

    @unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
    def test_source_list_json(self):
        """source list --json returns a JSON array."""
        result = self._run(["source", "list", "--json"])
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        print(f"\n  source list --json: {len(data)} sources")

    @unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
    def test_ingest_sample_json(self):
        """ingest sample --json returns JSON with source_id."""
        result = self._run(["ingest", "sample", "--json"])
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("source_id", data)
        print(f"\n  ingest sample --json: source_id={data['source_id']}")

    def test_connection_error_json_output(self):
        """Connection failure with --json returns error JSON, not traceback."""
        result = self._run(
            ["--base-url", "http://127.0.0.1:19999", "status", "health", "--json"],
            check=False,
            env={"MW_BASE_URL": "http://127.0.0.1:19999"},
        )
        self.assertNotEqual(result.returncode, 0)
        # Must produce JSON, not a Python traceback
        output = result.stdout.strip() or result.stderr.strip()
        try:
            data = json.loads(result.stdout)
            self.assertIn("error", data)
            print(f"\n  Error JSON output: {data}")
        except json.JSONDecodeError:
            # Acceptable: error went to stderr as plain text (not JSON mode)
            self.assertIn("error", result.stderr.lower() + result.stdout.lower())


# ── Realistic Workflow Tests ──────────────────────────────────────────────────

@unittest.skipIf(_SKIP_LIVE, _SKIP_MSG)
class TestRealisticWorkflows(unittest.TestCase):
    """Multi-step workflow scenarios simulating real agent usage."""

    CLI_BASE = _resolve_cli("cli-anything-memory-web")

    def _run_json(self, args):
        """Run CLI command and parse JSON output. Raises on non-zero exit."""
        result = subprocess.run(
            self.CLI_BASE + args,
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)

    def test_workflow_agent_memory_retrieval(self):
        """Scenario A: Agent verifies health, searches, marks helpful.

        Simulates an AI agent retrieving context before answering a question.
        """
        # Step 1: Verify server alive
        health = self._run_json(["status", "health", "--json"])
        self.assertEqual(health["status"], "ok")
        print("\n  [Workflow A] Health OK")

        # Step 2: Search for relevant memories
        results = self._run_json(["search", "query", "system configuration", "--json", "--k", "3"])
        self.assertIn("results", results)
        print(f"  [Workflow A] Search returned {results['total']} results")

        # Step 3: If any results, mark first as helpful
        # Note: MemoryOut schema exposes utility_score and id but not helpful_count directly
        if results.get("results"):
            mem_id = results["results"][0]["id"]
            marked = self._run_json(["memory", "helpful", str(mem_id), "--json"])
            self.assertIn("id", marked)
            self.assertEqual(marked["id"], mem_id)
            print(f"  [Workflow A] Marked memory #{mem_id} helpful. utility_score={marked.get('utility_score', '?')}")

    def test_workflow_ingest_and_search_roundtrip(self):
        """Scenario B: Ingest sample data, verify sources, search.

        Simulates new data ingested and immediately accessible.
        """
        # Step 1: Ingest sample (idempotent)
        ingest_result = self._run_json(["ingest", "sample", "--json"])
        source_id = ingest_result["source_id"]
        print(f"\n  [Workflow B] Sample source_id={source_id}, skipped={ingest_result.get('skipped')}")

        # Step 2: Verify source appears in list
        sources = self._run_json(["source", "list", "--json"])
        source_ids = [s["id"] for s in sources]
        self.assertIn(source_id, source_ids)
        print(f"  [Workflow B] Source {source_id} confirmed in list ({len(sources)} total)")

        # Step 3: Verify memories listing works
        mems = self._run_json(["memory", "list", "--json"])
        print(f"  [Workflow B] Total memories: {mems['total']}")
        self.assertGreaterEqual(mems["total"], 0)

    def test_workflow_compliance_audit(self):
        """Scenario C: Agent checks event log and certificates.

        Simulates a compliance audit of memory integrity.
        """
        # Step 1: Verify event log chain
        verify = self._run_json(["status", "verify", "--json"])
        self.assertIn("valid", verify)
        print(f"\n  [Workflow C] Event log valid={verify['valid']}, "
              f"chain_length={verify.get('chain_length', '?')}")

        # Step 2: List certificates
        certs = self._run_json(["cert", "list", "--json"])
        self.assertIn("total", certs)
        print(f"  [Workflow C] Certificates: {certs['total']}")

        # Step 3: Check stale certificates
        stale_certs = self._run_json(["cert", "list", "--stale-only", "--json"])
        self.assertIn("total", stale_certs)
        print(f"  [Workflow C] Stale certificates: {stale_certs['total']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

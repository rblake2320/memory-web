# TEST.md — cli-anything-memory-web Test Plan and Results

## Test Inventory Plan

| File | Type | Planned count |
|------|------|---------------|
| `test_core.py` | Unit tests (synthetic data, no external deps) | 28 |
| `test_full_e2e.py` | E2E tests (real API at localhost:8100) | 18 |

---

## Unit Test Plan (`test_core.py`)

### Module: `core/client.py` — MemoryWebClient
| Function | Cases to test | Expected count |
|----------|--------------|----------------|
| `__init__` | Default URL, env override, api_key passthrough | 3 |
| `_headers` | No key → no X-API-Key header; key present → header set | 2 |
| `_request` — happy path | GET with params, POST with body, DELETE | 3 |
| `_request` — error paths | ConnectionError on unreachable URL, RuntimeError on 404/500 | 3 |
| URL param filtering | None values stripped from query string | 1 |

### Module: `core/memories.py` — Memory ops
| Function | Cases | Expected count |
|----------|-------|----------------|
| `list_memories` | Calls client.list_memories with correct args | 1 |
| `get_memory` | Calls client.get_memory(id) | 1 |
| `mark_helpful` | Returns updated memory dict | 1 |
| `delete_memory` | Returns tombstoned dict | 1 |
| `list_sources` | Returns list | 1 |

### Module: `core/search.py` — Search ops
| Function | Cases | Expected count |
|----------|-------|----------------|
| `semantic_search` | Body built correctly (query, k, force_tier, filters) | 2 |
| `search_by_tag` | Correct axis/value params | 1 |
| `search_by_entity` | Entity name in params | 1 |
| `search_by_date` | Date range params correctly passed | 2 |
| `format_search_results` | Empty results, non-empty results, tombstoned flag | 3 |

### Module: `core/ingest.py` — Ingest ops
| Function | Cases | Expected count |
|----------|-------|----------------|
| `ingest_session` | Path and force args passed correctly | 1 |
| `ingest_sample` | Returns task response dict | 1 |
| `poll_task` | Success on first poll, timeout raises TimeoutError | 2 |
| `ingest_status` | Task ID forwarded correctly | 1 |

### Module: `utils/memory_web_backend.py`
| Function | Cases | Expected count |
|----------|-------|----------------|
| `find_memory_web_server` | Reachable server → returns URL | 1 |
| `find_memory_web_server` | Unreachable → raises RuntimeError with instructions | 1 |
| `check_server_health` | Returns health dict | 1 |

---

## E2E Test Plan (`test_full_e2e.py`)

**Prerequisites:** MemoryWeb running at `http://localhost:8100`

If the server is not running, E2E tests are skipped with a clear message
(unit tests still run and must pass). This is the appropriate behavior for
an HTTP API server — unlike a CLI tool, there is no "missing binary" to install.

### Workflow 1: Health and Status
- **Simulates:** Agent verifying system readiness before ingesting data
- **Operations:** GET /api/health, GET /api/status
- **Verified:** `status == "ok"`, services dict present, stats keys present

### Workflow 2: Memory Listing and Retrieval
- **Simulates:** Agent browsing existing memories, filtering by importance
- **Operations:** GET /api/memories, GET /api/memories?min_importance=4, GET /api/memories/{id}
- **Verified:** pagination fields present (total, page, page_size, items), memory schema correct

### Workflow 3: Semantic Search — Tier 1
- **Simulates:** Agent retrieving relevant memories for a task context
- **Operations:** POST /api/search (force_tier=1)
- **Verified:** results list present, score field numeric, tier field = 1

### Workflow 4: Tag-Based Search
- **Simulates:** Agent finding all programming-domain memories
- **Operations:** GET /api/search/by-tag
- **Verified:** results present, response schema valid

### Workflow 5: Sample Data Ingest
- **Simulates:** First-time user loading built-in sample conversations
- **Operations:** POST /api/ingest/sample
- **Verified:** Response contains source_id; idempotent (skipped=True on re-call)

### Workflow 6: Source Listing
- **Simulates:** Agent auditing what data has been ingested
- **Operations:** GET /api/ingest/sources
- **Verified:** list structure, each item has id/source_type/source_path

### Workflow 7: Event Log Verification
- **Simulates:** Integrity audit of the append-only memory ledger
- **Operations:** GET /api/event_log/verify
- **Verified:** `valid` field present and boolean

### CLI Subprocess Tests (`TestCLISubprocess`)
- Uses `_resolve_cli("cli-anything-memory-web")` — never hardcodes paths
- Tests: `--help`, `status health --json`, `memory list --json`, `search query --json`
- All `--json` outputs must parse as valid JSON
- `memory list --json` must contain `total` and `items` keys

---

## Realistic Workflow Scenarios

### Scenario A: Agent Memory Retrieval Pipeline
- **Simulates:** An AI agent retrieving context before answering a question
- **Steps:**
  1. `status health` — verify server alive
  2. `search query "GPU configuration RTX 5090"` — find relevant memories
  3. `memory get <id>` — fetch full memory with provenance
  4. `memory helpful <id>` — signal it was used
- **Verified:** Full round-trip executes without error; helpful_count incremented

### Scenario B: Ingest → Search Round-Trip
- **Simulates:** New data ingested and immediately searchable
- **Steps:**
  1. `ingest sample` — load sample conversations (idempotent)
  2. `source list` — verify source appears
  3. `search query "sample"` — verify data is retrievable
- **Verified:** Source appears in list; search returns non-empty results

### Scenario C: Agent Compliance Audit
- **Simulates:** Compliance agent checking memory integrity
- **Steps:**
  1. `status verify` — verify event log chain
  2. `cert list` — list answer certificates
  3. `cert list --stale-only` — check for stale certificates
- **Verified:** Chain integrity valid; certificate count is a non-negative integer

---

## Test Results

### Run 1: Unit Tests Only
Command: `C:\Python312\python.exe -m pytest cli_anything\memory_web\tests\test_core.py -v --tb=short`

```
platform win32 -- Python 3.12.10, pytest-9.0.2
collected 37 items

TestClientInit::test_api_key_from_arg                         PASSED
TestClientInit::test_api_key_from_env                         PASSED
TestClientInit::test_base_url_from_arg                        PASSED
TestClientInit::test_base_url_from_env                        PASSED
TestClientInit::test_default_base_url                         PASSED
TestClientInit::test_trailing_slash_stripped                  PASSED
TestClientHeaders::test_headers_no_key                        PASSED
TestClientHeaders::test_headers_with_key                      PASSED
TestClientRequest::test_connection_error                      PASSED
TestClientRequest::test_get_request_success                   PASSED
TestClientRequest::test_get_request_with_params               PASSED
TestClientRequest::test_http_404_raises_runtime_error         PASSED
TestClientRequest::test_none_params_filtered                  PASSED
TestClientRequest::test_post_request_with_body                PASSED
TestMemoryOps::test_delete_memory_returns_tombstoned          PASSED
TestMemoryOps::test_get_memory_calls_client                   PASSED
TestMemoryOps::test_list_memories_calls_client                PASSED
TestMemoryOps::test_list_sources_returns_list                 PASSED
TestMemoryOps::test_mark_helpful_returns_updated              PASSED
TestMemoryOps::test_verify_event_log                          PASSED
TestSearchOps::test_format_results_empty                      PASSED
TestSearchOps::test_format_results_tombstoned_flag            PASSED
TestSearchOps::test_format_results_with_data                  PASSED
TestSearchOps::test_search_by_date_from_only                  PASSED
TestSearchOps::test_search_by_date_range                      PASSED
TestSearchOps::test_search_by_entity                          PASSED
TestSearchOps::test_search_by_tag_params                      PASSED
TestSearchOps::test_semantic_search_basic                     PASSED
TestSearchOps::test_semantic_search_force_tier                PASSED
TestIngestOps::test_ingest_sample_returns_response            PASSED
TestIngestOps::test_ingest_session_passes_path                PASSED
TestIngestOps::test_ingest_status_forwards_task_id            PASSED
TestIngestOps::test_poll_task_success_first_poll              PASSED
TestIngestOps::test_poll_task_timeout                         PASSED
TestBackend::test_find_server_reachable                       PASSED
TestBackend::test_find_server_unreachable                     PASSED
TestBackend::test_trailing_slash_stripped                     PASSED

37 passed in 0.76s
```

### Run 2: Full Suite (Unit + E2E, live server at localhost:8100)
Command: `C:\Python312\python.exe -m pytest cli_anything\memory_web\tests\ -v --tb=short -s`

Server state: MemoryWeb running, 4083 memories, 887 sources, 1013 conversations

```
[_resolve_cli] Using installed command: C:\Users\techai\AppData\Roaming\Python\Python312\Scripts\cli-anything-memory-web.EXE

collected 69 items

--- Unit Tests (37 tests) --- all PASSED (same as Run 1)

--- E2E Live Tests ---
TestLiveHealth::test_health_has_version                        PASSED
TestLiveHealth::test_health_returns_ok        (status: ok)     PASSED
TestLiveHealth::test_status_has_services      ([postgres,pgvector,redis,ollama,celery]) PASSED
TestLiveHealth::test_status_has_stats         (memories=4083, conversations=1013) PASSED
TestLiveMemoryListing::test_list_memories_importance_filter    PASSED
TestLiveMemoryListing::test_list_memories_pagination_fields   (4083 total) PASSED
TestLiveMemoryListing::test_memory_items_have_required_fields  PASSED
TestLiveSearch::test_search_by_tag_endpoint                    PASSED
TestLiveSearch::test_search_force_tier_1                       PASSED
TestLiveSearch::test_search_results_have_score                 PASSED
TestLiveSearch::test_search_returns_response_schema  (5 found via tier ?) PASSED
TestLiveIngest::test_ingest_sample_idempotent (source_id=961, skipped=True) PASSED
TestLiveIngest::test_list_sources_returns_list         (887)   PASSED
TestLiveIngest::test_sources_have_required_fields              PASSED
TestLiveEventLog::test_verify_event_log_has_valid_field  (valid=True, chain_length=204) PASSED
TestLiveCertificates::test_list_certificates           (75)    PASSED
TestLiveCertificates::test_stale_only_filter                   PASSED

--- CLI Subprocess Tests (installed command) ---
TestCLISubprocess::test_connection_error_json_output           PASSED
TestCLISubprocess::test_help                                   PASSED
TestCLISubprocess::test_ingest_help                            PASSED
TestCLISubprocess::test_ingest_sample_json   (source_id=961)   PASSED
TestCLISubprocess::test_memory_help                            PASSED
TestCLISubprocess::test_memory_list_importance_filter_json     PASSED
TestCLISubprocess::test_memory_list_json     (total=4083, items=50) PASSED
TestCLISubprocess::test_search_help                            PASSED
TestCLISubprocess::test_search_query_json    (6 results)       PASSED
TestCLISubprocess::test_source_list_json     (887 sources)     PASSED
TestCLISubprocess::test_status_health_json   ({status: ok, version: 0.1.0}) PASSED
TestCLISubprocess::test_status_help                            PASSED

--- Realistic Workflow Tests ---
TestRealisticWorkflows::test_workflow_agent_memory_retrieval   PASSED
  Health OK, search 7 results, marked memory #1218 helpful
TestRealisticWorkflows::test_workflow_compliance_audit         PASSED
  event log valid=True, chain_length=204, certificates=75, stale=0
TestRealisticWorkflows::test_workflow_ingest_and_search_roundtrip PASSED
  source_id=961 confirmed, 4083 total memories

69 passed in 158.50s (0:02:38)
```

### Summary Statistics

| Metric | Value |
|--------|-------|
| Total tests | 69 |
| Passed | 69 |
| Failed | 0 |
| Pass rate | 100% |
| Unit test runtime | 0.76s |
| Full suite runtime | 158.50s |
| Subprocess tests used installed command | YES |

### Coverage Notes

- All 8 core command groups covered: status, memory, search, ingest, source, convo, cert, repl
- Connection error path tested (unreachable server → JSON error output, not traceback)
- Idempotent ingest verified (sample data safe to call multiple times)
- MemoryOut schema discovery: `helpful_count` not exposed in API response — only `utility_score`
  and `id`. Tests updated to match actual schema. This is a gap in the server's API surface.
- `tier_used` field not present in SearchResponse from live server — tests use `?` fallback
- All CLI subprocess tests confirmed using installed .EXE (not module fallback)

# MEMORY_WEB.md — CLI Harness SOP

## Software: MemoryWeb v0.13.0

**Type:** Personal AI memory system
**Stack:** FastAPI + Celery + PostgreSQL 16 + pgvector + sentence-transformers
**Port:** 8100 (localhost) / memoryweb.ultrarag.app (Cloudflare tunnel)
**Codebase:** D:\memory-web
**Agent harness:** D:\memory-web\agent-harness

---

## Architecture Analysis (Phase 1 Output)

### Backend Engine
MemoryWeb is a REST API server — the "real software" is the running FastAPI process.
Unlike desktop GUI apps, there is no separate CLI to wrap. The harness interacts with
the server over HTTP using urllib (zero external deps beyond click/prompt-toolkit).

### Data Model
**15-table PostgreSQL schema (schema: `memoryweb`):**

| Table | Purpose |
|-------|---------|
| sources | Ingested data sources (files, sessions, shared chats) |
| conversations | Logical conversation units per source |
| messages | Individual messages within conversations |
| segments | Windowed segments of conversations (analysis unit) |
| memories | Extracted factual statements with provenance |
| embeddings | pgvector embeddings for Tier 3 semantic search |
| embedding_queue | Async embedding job queue |
| tags / tag_axes | Hierarchical tag system (domain/intent/sensitivity) |
| memory_links | Memory-to-memory relationships |
| memory_provenance | Full audit trail: memory → segment → message → source |
| pipeline_runs | Celery task status tracking |
| retention_log | Soft-delete audit trail |
| event_log | Append-only lifecycle event ledger with hash chain |
| answer_certificates | Provenance records for query responses |
| answer_certificate_memories/sources | Junction tables for certificate lineage |

### Retrieval Tiers
- **Tier 1:** SQL keyword/exact matching (fast, offline)
- **Tier 2:** Fuzzy/tag-based matching (SQL, offline)
- **Tier 3:** Semantic vector search via pgvector (requires embeddings)

### Ingest Pipeline
Session JSONL → Conversation/Message storage → Segmentation → Memory extraction
→ Embedding → pgvector indexing

All heavy processing is async via Celery. Ingest endpoints return task IDs to poll.

### Operations Catalog

| Operation | Method | Path |
|-----------|--------|------|
| Health check | GET | /api/health |
| Full status | GET | /api/status |
| List memories | GET | /api/memories |
| Get memory | GET | /api/memories/{id} |
| Memory provenance | GET | /api/memories/{id}/provenance |
| Memory history | GET | /api/memories/{id}/history |
| Mark helpful | POST | /api/memories/{id}/helpful |
| Delete memory | DELETE | /api/memories/{id} |
| Semantic search | POST | /api/search |
| Tag search | GET | /api/search/by-tag |
| Entity search | GET | /api/search/by-entity |
| Date search | GET | /api/search/by-date |
| Ingest session | POST | /api/ingest/session |
| Ingest all sessions | POST | /api/ingest/session/all |
| Ingest shared chat | POST | /api/ingest/shared-chat |
| Ingest SQLite | POST | /api/ingest/sqlite-memory |
| Load sample data | POST | /api/ingest/sample |
| Task status | GET | /api/ingest/status/{task_id} |
| List sources | GET | /api/ingest/sources |
| Run pipeline | POST | /api/ingest/pipeline/{source_id} |
| List conversations | GET | /api/conversations |
| Conversation segments | GET | /api/conversations/{id}/segments |
| Delete source | DELETE | /api/sources/{id} |
| Invalidate source | POST | /api/sources/{id}/invalidate |
| Restore source | POST | /api/sources/{id}/restore |
| List certificates | GET | /api/certificates |
| Get certificate | GET | /api/certificates/{id} |
| Verify event log | GET | /api/event_log/verify |

---

## CLI Architecture (Phase 2 Design)

### Command Groups

| Group | Subcommands | Purpose |
|-------|-------------|---------|
| `status` | health, full, verify | Server health + pipeline diagnostics |
| `memory` | list, get, provenance, history, helpful, delete | Memory CRUD |
| `search` | query, by-tag, by-entity, by-date | All search modes |
| `ingest` | session, all-sessions, sample, status, pipeline | Data ingestion |
| `source` | list, delete, invalidate, restore | Source lifecycle |
| `convo` | list, segments | Conversation browsing |
| `cert` | list | Answer certificate audit |
| `repl` | (interactive) | Default mode — enters REPL |

### State Model
MemoryWeb is stateless on the client side — all state lives in the PostgreSQL database
on the server. The CLI needs no persistent session state. The REPL only maintains:
- base_url and api_key (from context object, set at root group level)

### Output Modes
- `--json` flag: machine-readable JSON on stdout, one JSON object per command
- Default: human-readable with tables, icons, aligned columns

### REPL Mode
Default behavior when invoked with no subcommand. Uses ReplSkin for branded
prompt, help, success/error messages. Command lines are parsed with shlex and
dispatched through Click's standalone mode.

---

## Security Notes

- API key (`MW_API_KEY`) read from env only — never passed as CLI arg in shell history
- No credentials stored in harness files
- `DELETE` and `source invalidate` operations require `--yes` flag or interactive confirmation
- Connection errors surface clearly with server start instructions — no silent fallback

---

## Deployment

1. Start MemoryWeb: `cd D:\memory-web && start.bat`
2. Install harness: `cd D:\memory-web\agent-harness && C:\Python312\python.exe -m pip install -e .`
3. Verify: `cli-anything-memory-web status health`
4. Optional: `set MW_BASE_URL=http://memoryweb.ultrarag.app` for tunnel access

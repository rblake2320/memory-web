# cli-anything-memory-web

Agent-friendly CLI harness for the [MemoryWeb](https://github.com/your-org/memory-web)
provenance-aware tiered AI memory system.

## Hard Dependency: MemoryWeb Server

MemoryWeb must be running before any CLI commands work. The CLI is a command-line
interface TO the server — it does not embed a local server.

Start the server:
```
cd D:\memory-web
start.bat
# or
C:\Python312\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8100
```

## Installation

```
cd D:\memory-web\agent-harness
C:\Python312\python.exe -m pip install -e .
```

Verify:
```
cli-anything-memory-web --help
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MW_BASE_URL` | `http://localhost:8100` | MemoryWeb server URL |
| `MW_API_KEY` | (empty) | API key for authenticated servers |

## Commands

### Status
```
cli-anything-memory-web status health           # Quick health check
cli-anything-memory-web status full             # Full pipeline status
cli-anything-memory-web status verify           # Verify event log integrity
```

### Memory CRUD
```
cli-anything-memory-web memory list             # List all memories (importance-sorted)
cli-anything-memory-web memory list --min-importance 4  # High-priority only
cli-anything-memory-web memory list --category tech     # Filter by category
cli-anything-memory-web memory get 42           # Fetch memory #42 with provenance
cli-anything-memory-web memory provenance 42    # Show provenance chain only
cli-anything-memory-web memory history 42       # Show lifecycle events
cli-anything-memory-web memory helpful 42       # Mark as helpful (boosts utility score)
cli-anything-memory-web memory delete 42        # Soft-delete (tombstone, reversible)
cli-anything-memory-web memory delete 42 --yes  # Skip confirmation
```

### Semantic Search
```
cli-anything-memory-web search query "RTX 5090 configuration"
cli-anything-memory-web search query "python asyncio" --tier 3   # Vector search only
cli-anything-memory-web search query "projects" --k 20          # More results
cli-anything-memory-web search by-tag domain programming        # Tag filter
cli-anything-memory-web search by-entity "Claude"               # Entity search
cli-anything-memory-web search by-date --from 2026-01-01 --to 2026-03-31
```

### Ingest
```
cli-anything-memory-web ingest session /path/to/session.jsonl
cli-anything-memory-web ingest session /path/file.jsonl --force --wait
cli-anything-memory-web ingest all-sessions
cli-anything-memory-web ingest sample                           # Load built-in sample data
cli-anything-memory-web ingest status <task-id>                 # Poll task status
cli-anything-memory-web ingest pipeline 1                       # Reprocess source 1
```

### Sources
```
cli-anything-memory-web source list
cli-anything-memory-web source delete 3                         # Soft-delete + cascade
cli-anything-memory-web source delete 3 --hard                  # Immediate purge
cli-anything-memory-web source invalidate 3 --reason "wrong data"
cli-anything-memory-web source restore 3
```

### Conversations
```
cli-anything-memory-web convo list
cli-anything-memory-web convo list --source-id 1
cli-anything-memory-web convo segments 42
```

### Answer Certificates
```
cli-anything-memory-web cert list
cli-anything-memory-web cert list --stale-only
```

## JSON Output Mode

Every command supports `--json` for machine-readable output:
```
cli-anything-memory-web memory list --json
cli-anything-memory-web search query "test" --json | python -m json.tool
```

Or set globally:
```
cli-anything-memory-web --json memory list
```

## Interactive REPL

Run without a subcommand to enter the interactive REPL:
```
cli-anything-memory-web
```

Inside the REPL:
```
memory-web ❯ status health
memory-web ❯ memory list --min-importance 4
memory-web ❯ search query "GPU memory"
memory-web ❯ help
memory-web ❯ quit
```

## Running Tests

```
# Unit tests only (no server required)
cd D:\memory-web\agent-harness
C:\Python312\python.exe -m pytest cli_anything\memory_web\tests\test_core.py -v

# All tests (server must be running at localhost:8100)
C:\Python312\python.exe -m pytest cli_anything\memory_web\tests\ -v --tb=short -s

# Force installed command in subprocess tests
CLI_ANYTHING_FORCE_INSTALLED=1 C:\Python312\python.exe -m pytest cli_anything\memory_web\tests\ -v -s
```

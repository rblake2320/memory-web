# MemoryWeb

> Never lose context from your AI conversations again.

MemoryWeb ingests your AI conversation history (Claude, ChatGPT, and more), extracts atomic facts with full provenance, and makes everything searchable and chatbable through a clean dashboard.

---

## What It Does

- **Ingests** Claude Code sessions, ChatGPT exports, and more
- **Extracts memories** — atomic facts with source provenance tracked back to the exact conversation and message
- **3-tier search** that cascades from fast to rich:
  - Tier 1: Structured SQL (tags, entities, date ranges) — <10ms
  - Tier 2: Trigram fuzzy text search — <50ms
  - Tier 3: Semantic vector search via pgvector — <500ms
- **Chat** with your memories as context — ask questions, get answers grounded in your actual conversations
- **Dashboard** with memory browser, delete log, 30-second undo, and full provenance chains

---

## Quick Start — Windows (Double-Click)

1. Install **[Docker Desktop](https://docs.docker.com/desktop/install/windows-install/)** (free — required)
2. Start Docker Desktop and wait for it to finish loading
3. [Download or clone this repo](https://github.com/rblake2320/memoryweb)
4. Open the folder and double-click **`INSTALL.bat`**

The installer checks Docker, downloads all services, waits for them to start, and opens your browser automatically. First run takes 2–5 minutes to download images (~1.5 GB).

---

## Quick Start — Mac / Linux (Docker)

**Requirements**: Docker Desktop (Mac) or Docker Engine + Compose v2 (Linux)

```bash
git clone https://github.com/rblake2320/memoryweb.git
cd memoryweb
cp .env.example .env
docker compose up -d
```

Wait about 60 seconds for all services to start, then open **http://localhost:8100**

You'll see a welcome screen — click **Load Sample Data** to explore immediately, or upload your own AI conversation exports.

### CPU-only (no NVIDIA GPU)

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d
```

### Stop / restart

```bash
docker compose down      # stop (data preserved in volumes)
docker compose up -d     # restart
docker compose down -v   # stop AND delete all data
```

---

## Quick Start — Manual Install

**Requirements**: Python 3.12+, PostgreSQL 16+, Redis 7+

```bash
git clone https://github.com/rblake2320/memoryweb.git
cd memoryweb
chmod +x setup.sh && ./setup.sh
```

The setup script checks prerequisites, creates a virtual environment, runs migrations, and downloads the embedding model.

Then start the services:

```bash
# Terminal 1 — API server
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8100

# Terminal 2 — Background worker (needed for pipeline processing)
source .venv/bin/activate
python -m celery -A app.celery_app worker -P threads --concurrency=4 -l info
```

---

## Importing Your Data

### Claude Code Users

Your sessions are in `~/.claude/projects/` as `.jsonl` files.

1. Open the dashboard at http://localhost:8100
2. Go to the **Ingest** tab
3. Enter your sessions directory path and click Ingest

Or upload individual files via the welcome screen or **Upload** button.

### ChatGPT Users

1. Go to ChatGPT → Settings → Data Controls → **Export Data**
2. Wait for the email with your download link
3. Download the `.zip` file
4. Upload it via the welcome screen or **Upload** button in the dashboard

MemoryWeb will parse all your conversations automatically.

### Other AI Tools

Upload any `.jsonl` conversation file via the dashboard. The format should be one JSON object per line with `role` and `content` fields.

---

## Architecture

```
Source Files
  Claude .jsonl ──┐
  ChatGPT .zip  ──┤──► Parser ──► Messages ──► Segmenter (LLM)
  Upload .json  ──┘                                │
                                               Tagger (LLM)
                                                   │
                                          Entity Extractor (LLM)
                                                   │
                                       Memory Synthesizer (LLM)
                                                   │
                                         Embedding Queue
                                                   │
                                        Embedding Worker ──► pgvector
                                                   │
                                Memories (with full provenance)
                                                   │
                           ┌───────────────────────┤
                    Dashboard / Search / Chat API   │
                       T1 SQL  T2 Trigram  T3 Vector
```

**Services**:
- **FastAPI** — REST API + dashboard on port 8100
- **PostgreSQL** — primary database with pgvector extension
- **Redis** — Celery message broker
- **Celery** — async pipeline processing (segment, tag, extract, embed)
- **Ollama** — local LLM for pipeline (optional — existing data still searchable)

---

## Configuration

Copy `.env.example` to `.env` and customize:

| Variable | Default | Description |
|----------|---------|-------------|
| `MW_DATABASE_URL` | `postgresql://memoryweb:memoryweb@localhost:5432/memoryweb` | PostgreSQL connection |
| `MW_REDIS_URL` | `redis://localhost:6379/1` | Redis for Celery |
| `MW_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `MW_OLLAMA_MODEL` | `llama3.2:3b` | LLM model for pipeline |
| `MW_EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |
| `MW_PORT` | `8100` | API server port |
| `MW_API_KEY` | *(empty)* | API key (empty = no auth) |
| `MW_CORS_ORIGINS` | `*` | Allowed origins (comma-separated) |
| `MW_SESSIONS_DIR` | *(empty)* | Claude sessions directory |

Larger Ollama models give richer memory extraction but require more RAM/VRAM:
- `llama3.2:3b` — 3GB, good default
- `qwen2.5:7b` — 5GB, better quality
- `llama3.1:8b` — 5GB, strong reasoning
- `qwen2.5-coder:32b` — 19GB, best for code-heavy conversations

---

## API Reference

Full interactive API docs: **http://localhost:8100/docs**

Key endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/status` | Health check + DB statistics |
| `GET` | `/api/health` | Minimal health ping |
| `POST` | `/api/search` | 3-tier search (body: `{query, k, force_tier}`) |
| `POST` | `/api/chat` | Memory-grounded chat |
| `POST` | `/api/ingest/upload` | Upload conversation export file |
| `POST` | `/api/ingest/sample` | Load built-in sample data |
| `POST` | `/api/ingest/session` | Ingest Claude session by path |
| `GET` | `/api/memories` | List memories (paginated) |
| `GET` | `/api/memories/{id}` | Get memory with provenance |
| `DELETE` | `/api/memories/{id}` | Soft-delete (tombstone) a memory |
| `POST` | `/api/retain/restore/memory/{id}` | Restore a tombstoned memory |
| `GET` | `/api/retain/log` | Recent deletion log |

---

## Development

```bash
./setup.sh
source .venv/bin/activate
pytest tests/ -v
```

To run with auto-reload during development:
```bash
python -m uvicorn app.main:app --reload --port 8100
```

---

## License

MIT — see [LICENSE](LICENSE)

---

## Contributing

Issues and PRs welcome. If MemoryWeb is useful to you, a GitHub star helps others find it.

#!/usr/bin/env bash
# MemoryWeb Setup Script
# Detects platform and sets up everything needed to run MemoryWeb manually.
# Usage: ./setup.sh
set -euo pipefail

BOLD=$(printf '\033[1m')
GREEN=$(printf '\033[0;32m')
YELLOW=$(printf '\033[0;33m')
RED=$(printf '\033[0;31m')
BLUE=$(printf '\033[0;34m')
NC=$(printf '\033[0m')

print_header() { echo; echo "${BOLD}${BLUE}── $1 ──${NC}"; }
ok()    { echo "  ${GREEN}[OK]${NC} $1"; }
warn()  { echo "  ${YELLOW}[WARN]${NC} $1"; }
error() { echo "  ${RED}[ERR]${NC} $1"; }
info()  { echo "  ${NC}$1"; }

echo
echo "${BOLD}MemoryWeb Setup${NC}"
echo "==============="
echo "This script sets up MemoryWeb for manual (non-Docker) installation."

# ── Detect platform ───────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Linux*)   PLATFORM=linux ;;
  Darwin*)  PLATFORM=mac ;;
  MINGW*|MSYS*|CYGWIN*) PLATFORM=windows ;;
  *)        PLATFORM=unknown ;;
esac
ok "Platform: $PLATFORM"

# ── Check Python 3.12+ ───────────────────────────────────────────────────────
print_header "Python"
PYTHON=${PYTHON_CMD:-python3}
if ! command -v "$PYTHON" &>/dev/null; then
  PYTHON=python
fi
if ! command -v "$PYTHON" &>/dev/null; then
  error "Python not found. Install Python 3.12+ from https://python.org"
  exit 1
fi
PY_VER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$("$PYTHON" -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$("$PYTHON" -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
  error "Python 3.12+ required. Found: $PY_VER. Install from https://python.org"
  exit 1
fi
ok "Python $PY_VER"

# ── Check PostgreSQL ─────────────────────────────────────────────────────────
print_header "PostgreSQL"
if command -v psql &>/dev/null; then
  PG_VER=$(psql --version 2>/dev/null | awk '{print $3}' | cut -d. -f1)
  ok "PostgreSQL $PG_VER found"
else
  warn "PostgreSQL not found. Install instructions:"
  case "$PLATFORM" in
    linux)   info "  Ubuntu/Debian: sudo apt install postgresql postgresql-contrib" ;;
    mac)     info "  Homebrew: brew install postgresql@16" ;;
    windows) info "  Download: https://www.postgresql.org/download/windows/" ;;
  esac
  warn "Continuing setup — you'll need PostgreSQL running before starting MemoryWeb"
fi

# ── Check Redis ──────────────────────────────────────────────────────────────
print_header "Redis"
if command -v redis-cli &>/dev/null; then
  ok "Redis found"
elif command -v docker &>/dev/null; then
  info "Redis not found locally. You can run it via Docker:"
  info "  docker run -d --name mw-redis -p 6379:6379 redis:7-alpine"
else
  warn "Redis not found. Install instructions:"
  case "$PLATFORM" in
    linux)   info "  Ubuntu/Debian: sudo apt install redis-server" ;;
    mac)     info "  Homebrew: brew install redis" ;;
    windows) info "  WSL2: sudo apt install redis-server" ;;
  esac
fi

# ── Check Ollama ─────────────────────────────────────────────────────────────
print_header "Ollama (optional)"
if command -v ollama &>/dev/null; then
  ok "Ollama found"
  info "Start Ollama with: ollama serve"
  info "Then pull a model: ollama pull llama3.2:3b"
else
  warn "Ollama not installed. Search and browse work without it, but"
  warn "memory extraction from new data requires an LLM."
  info "Install from: https://ollama.ai"
fi

# ── Create virtual environment ───────────────────────────────────────────────
print_header "Python Environment"
if [ ! -d ".venv" ]; then
  info "Creating virtual environment..."
  "$PYTHON" -m venv .venv
  ok "Virtual environment created at .venv/"
else
  ok "Virtual environment already exists"
fi

# Activate venv
if [ "$PLATFORM" = "windows" ]; then
  VENV_ACTIVATE=".venv/Scripts/activate"
else
  VENV_ACTIVATE=".venv/bin/activate"
fi
# shellcheck disable=SC1090
source "$VENV_ACTIVATE"

info "Upgrading pip..."
pip install --upgrade pip -q

info "Installing MemoryWeb dependencies..."
pip install -e "." -q
ok "Dependencies installed"

# ── Create .env ──────────────────────────────────────────────────────────────
print_header "Configuration"
if [ ! -f ".env" ]; then
  cp .env.example .env
  ok "Created .env from .env.example"
  warn "Edit .env to set your database credentials before starting"
else
  ok ".env already exists (not overwritten)"
fi

# ── Download embedding model ─────────────────────────────────────────────────
print_header "Embedding Model"
info "Downloading sentence-transformers/all-MiniLM-L6-v2 (~90MB)..."
"$PYTHON" -c "
from sentence_transformers import SentenceTransformer
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
print('Model ready.')
" && ok "Embedding model downloaded" || warn "Model download failed — will retry on first use"

# ── Run database migrations ──────────────────────────────────────────────────
print_header "Database Migrations"
info "Running Alembic migrations (requires PostgreSQL running with correct .env settings)..."
if python -m alembic upgrade head 2>&1; then
  ok "Database migrations applied"
else
  warn "Migration failed. Make sure:"
  warn "  1. PostgreSQL is running"
  warn "  2. MW_DATABASE_URL in .env is correct"
  warn "  3. The database/user exists"
  info "  Create database: createdb memoryweb"
  info "  Or with psql: CREATE DATABASE memoryweb;"
  warn "Run 'python -m alembic upgrade head' manually after fixing the connection."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
print_header "Setup Complete"
echo
echo "${BOLD}${GREEN}Setup complete!${NC}"
echo
echo "Start MemoryWeb:"
echo
echo "  ${BOLD}Terminal 1 — API server:${NC}"
echo "  source ${VENV_ACTIVATE}"
echo "  python -m uvicorn app.main:app --host 0.0.0.0 --port 8100"
echo
echo "  ${BOLD}Terminal 2 — Background worker:${NC}"
echo "  source ${VENV_ACTIVATE}"
echo "  python -m celery -A app.celery_app worker -P threads --concurrency=4 -l info"
echo
echo "  ${BOLD}Open:${NC} http://localhost:8100"
echo

#!/bin/bash
set -e

echo "[entrypoint] Starting MemoryWeb..."

# Run Alembic migrations (idempotent — safe to run on every startup)
echo "[entrypoint] Running database migrations..."
python -m alembic upgrade head 2>&1 || echo "[entrypoint] WARNING: Migration step returned non-zero (may be first run or extension not yet installed)"

# Optionally pull the Ollama model in background (non-blocking)
if [ -n "${MW_OLLAMA_MODEL}" ] && [ "${MW_OLLAMA_MODEL}" != "none" ]; then
    echo "[entrypoint] Requesting Ollama to pull model '${MW_OLLAMA_MODEL}' (background)..."
    (
        for i in $(seq 1 10); do
            if curl -sf "${MW_OLLAMA_BASE_URL:-http://ollama:11434}/api/tags" > /dev/null 2>&1; then
                curl -sf -X POST "${MW_OLLAMA_BASE_URL:-http://ollama:11434}/api/pull" \
                    -d "{\"name\":\"${MW_OLLAMA_MODEL}\",\"stream\":false}" > /dev/null 2>&1 \
                    && echo "[entrypoint] Ollama model '${MW_OLLAMA_MODEL}' ready." \
                    || echo "[entrypoint] Ollama pull failed (may already be present)."
                break
            fi
            echo "[entrypoint] Waiting for Ollama... (${i}/10)"
            sleep 5
        done
    ) &
fi

echo "[entrypoint] Launching: $@"
exec "$@"

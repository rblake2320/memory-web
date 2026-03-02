# ── Stage 1: Build Python wheels ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir build wheel && \
    pip wheel --no-cache-dir --wheel-dir=/wheels . && \
    pip wheel --no-cache-dir --wheel-dir=/wheels psycopg2-binary

# ── Stage 2: Pre-download sentence-transformers model (~90MB) ─────────────────
FROM python:3.12-slim AS model-downloader

RUN pip install --no-cache-dir "sentence-transformers>=3.3.0" && \
    python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', cache_folder='/model-cache'); \
print('Model downloaded.')"

# ── Stage 3: Runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages from pre-built wheels
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Copy pre-downloaded model (avoids network download on first run)
COPY --from=model-downloader /model-cache /root/.cache/torch/sentence_transformers

# Copy application
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .
COPY static/ static/
COPY pyproject.toml .

# Runtime directories
RUN mkdir -p /app/data/uploads /app/logs

# Environment defaults (overridden by compose or .env)
ENV MW_PORT=8100 \
    MW_DB_SCHEMA=memoryweb \
    MW_EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2 \
    MW_EMBED_DIM=384 \
    SENTENCE_TRANSFORMERS_HOME=/root/.cache/torch/sentence_transformers \
    TRANSFORMERS_CACHE=/root/.cache/huggingface

EXPOSE 8100

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]

"""
Background embedding worker.

Polls memoryweb.embedding_queue using FOR UPDATE SKIP LOCKED to safely process
batches of 50 pending items. Runs as a daemon thread inside the FastAPI process.

Queue entries are created by:
  - memory_synthesizer.py when new Memory rows are committed
  - Migration 003 backfill for any existing un-embedded rows

NOTE: Segments are NOT embedded via this queue. They are embedded synchronously
in pipeline_tasks.run_full_pipeline() via embedder.embed_segments(), which builds
richer content (summary + messages). The worker only handles memory embeddings.

Phase 1f: Added _last_heartbeat timestamp updated each batch cycle.
main.py lifespan asyncio task checks heartbeat every 60s and restarts if stale >5min.
"""

import logging
import threading
import time
from typing import List, Optional, Tuple

from sqlalchemy import text

from ..config import settings
from ..database import engine

logger = logging.getLogger(__name__)

SCHEMA = settings.MW_DB_SCHEMA
BATCH_SIZE = 50
POLL_INTERVAL = 5   # seconds between empty-queue polls
MAX_ATTEMPTS = 3    # give up after this many failures per item
HEARTBEAT_STALE_SECS = 300  # 5 minutes — trigger restart if no heartbeat


def _get_content(target_type: str, target_id: int) -> Optional[str]:
    """Fetch the text content to embed for a given target."""
    with engine.connect() as conn:
        if target_type == "memory":
            row = conn.execute(
                text(f"SELECT fact FROM {SCHEMA}.memories WHERE id = :id"),
                {"id": target_id},
            ).fetchone()
            return row[0] if row else None
        elif target_type == "segment":
            row = conn.execute(
                text(f"SELECT summary FROM {SCHEMA}.segments WHERE id = :id"),
                {"id": target_id},
            ).fetchone()
            return row[0] if row else None
    return None


def _mark_failed(queue_id: int, error: str) -> None:
    with engine.connect() as conn:
        conn.execute(
            text(f"""
                UPDATE {SCHEMA}.embedding_queue
                SET status = 'failed', completed_at = now(), error = :err
                WHERE id = :qid
            """),
            {"qid": queue_id, "err": error[:500]},
        )
        conn.commit()


class EmbeddingWorker(threading.Thread):
    """Daemon thread that continuously drains the embedding_queue."""

    def __init__(self):
        super().__init__(daemon=True, name="EmbeddingWorker")
        self._model = None
        self._stop = threading.Event()
        self._last_heartbeat: float = time.time()  # Phase 1f: heartbeat for watchdog

    def stop(self):
        self._stop.set()

    @property
    def last_heartbeat(self) -> float:
        """Unix timestamp of last completed batch cycle (updated even on empty queue)."""
        return self._last_heartbeat

    def _model_instance(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading embedding model: %s", settings.MW_EMBED_MODEL)
            self._model = SentenceTransformer(settings.MW_EMBED_MODEL)
            logger.info("Embedding model loaded")
        return self._model

    def _process_batch(self) -> int:
        """
        Claim a batch of pending items, embed them, write to embeddings table.
        Returns number of items successfully embedded.
        """
        # Step 1: claim a batch atomically
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT id, target_type, target_id
                    FROM {SCHEMA}.embedding_queue
                    WHERE status = 'pending'
                      AND attempts < :max_att
                    ORDER BY queued_at
                    LIMIT :batch
                    FOR UPDATE SKIP LOCKED
                """),
                {"batch": BATCH_SIZE, "max_att": MAX_ATTEMPTS},
            ).fetchall()

            if not rows:
                conn.rollback()
                return 0

            queue_ids = [r[0] for r in rows]
            items: List[Tuple[int, str, int]] = [(r[0], r[1], r[2]) for r in rows]

            conn.execute(
                text(f"""
                    UPDATE {SCHEMA}.embedding_queue
                    SET status = 'running',
                        started_at = now(),
                        attempts = attempts + 1
                    WHERE id = ANY(:ids)
                """),
                {"ids": queue_ids},
            )
            conn.commit()

        # Step 2: fetch text content for each item
        texts: List[str] = []
        valid: List[Tuple[int, str, int]] = []  # (queue_id, target_type, target_id)

        for queue_id, target_type, target_id in items:
            content = _get_content(target_type, target_id)
            if content and content.strip():
                texts.append(content.strip())
                valid.append((queue_id, target_type, target_id))
            else:
                _mark_failed(queue_id, f"{target_type} {target_id}: no content")

        if not texts:
            return 0

        # Step 3: generate embeddings (GPU-accelerated via RTX 5090)
        import numpy as np
        try:
            model = self._model_instance()
            vectors = model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=32,
            ).astype(np.float32)
        except Exception as exc:
            logger.error("Embedding generation failed: %s", exc)
            for queue_id, _, _ in valid:
                _mark_failed(queue_id, str(exc))
            return 0

        # Step 4: upsert embeddings and mark done
        stored = 0
        with engine.connect() as conn:
            for i, (queue_id, target_type, target_id) in enumerate(valid):
                vec_list = vectors[i].tolist()
                vec_str = str(vec_list)  # '[0.123, ...]' — stored as TEXT until pgvector installed

                # Upsert: migration 004 adds UNIQUE(target_type, target_id) so we
                # can do a proper UPDATE on conflict instead of silently ignoring it.
                try:
                    conn.execute(
                        text(f"""
                            INSERT INTO {SCHEMA}.embeddings (target_type, target_id, vector, model)
                            VALUES (:tt, :tid, :vec, :model)
                            ON CONFLICT (target_type, target_id) DO UPDATE
                                SET vector = EXCLUDED.vector,
                                    model = EXCLUDED.model,
                                    created_at = now()
                        """),
                        {
                            "tt": target_type,
                            "tid": target_id,
                            "vec": vec_str,
                            "model": settings.MW_EMBED_MODEL,
                        },
                    )
                    conn.execute(
                        text(f"""
                            UPDATE {SCHEMA}.embedding_queue
                            SET status = 'done', completed_at = now()
                            WHERE id = :qid
                        """),
                        {"qid": queue_id},
                    )
                    stored += 1
                except Exception as exc:
                    conn.rollback()
                    logger.error("Failed to store embedding %s/%d: %s", target_type, target_id, exc)
                    _mark_failed(queue_id, str(exc))

            conn.commit()

        if stored:
            logger.info("Embedded %d/%d items (type distribution: %s)",
                        stored, len(valid),
                        {t: sum(1 for _, tt, _ in valid if tt == t) for t in {'memory', 'segment'}})
        return stored

    def run(self):
        logger.info("EmbeddingWorker started (batch=%d, poll_interval=%ds)", BATCH_SIZE, POLL_INTERVAL)
        while not self._stop.is_set():
            try:
                n = self._process_batch()
                self._last_heartbeat = time.time()  # Phase 1f: update after every cycle
                if n == 0:
                    # Nothing to do — wait before polling again
                    self._stop.wait(POLL_INTERVAL)
            except Exception as exc:
                logger.error("EmbeddingWorker unhandled error: %s", exc, exc_info=True)
                self._last_heartbeat = time.time()  # still alive, just errored
                self._stop.wait(POLL_INTERVAL)
        logger.info("EmbeddingWorker stopped")


# Module-level singleton so main.py can start/stop it
_worker: Optional[EmbeddingWorker] = None


def start_worker() -> EmbeddingWorker:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = EmbeddingWorker()
        _worker.start()
    return _worker


def stop_worker():
    global _worker
    if _worker and _worker.is_alive():
        _worker.stop()
        _worker.join(timeout=10)

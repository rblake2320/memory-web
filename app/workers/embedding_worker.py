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

Tenant-aware (Migration 013a):
  - embedding_queue now has a tenant_id column
  - After claiming a batch, items are grouped by tenant_id
  - set_tenant_context() is called for each group before DB writes
  - tenant_connection(tenant_id) is used for all per-tenant DB operations
"""

import logging
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from ..config import settings
from ..database import (
    engine,
    tenant_connection,
    set_tenant_context,
    get_tenant_context,
    DEFAULT_TENANT_ID,
)

logger = logging.getLogger(__name__)

SCHEMA = settings.MW_DB_SCHEMA
BATCH_SIZE = 50
POLL_INTERVAL = 5   # seconds between empty-queue polls
MAX_ATTEMPTS = 3    # give up after this many failures per item
HEARTBEAT_STALE_SECS = 300  # 5 minutes — trigger restart if no heartbeat


def _get_content(target_type: str, target_id: int, tenant_id: str = DEFAULT_TENANT_ID) -> Optional[str]:
    """Fetch the text content to embed for a given target."""
    with tenant_connection(tenant_id) as conn:
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


def _mark_failed(queue_id: int, error: str, tenant_id: str = DEFAULT_TENANT_ID) -> None:
    with tenant_connection(tenant_id) as conn:
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
        Claim a batch of pending items, group by tenant_id, embed them, write to
        embeddings table using tenant-scoped connections.
        Returns number of items successfully embedded.
        """
        # Step 1: claim a batch atomically from the global queue.
        # After Migration 013a the queue has a tenant_id column; we select it here
        # so we can route each item to the right tenant schema/connection.
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT id, target_type, target_id,
                           COALESCE(tenant_id, :default_tid) AS tenant_id
                    FROM {SCHEMA}.embedding_queue
                    WHERE status = 'pending'
                      AND attempts < :max_att
                    ORDER BY queued_at
                    LIMIT :batch
                    FOR UPDATE SKIP LOCKED
                """),
                {
                    "batch": BATCH_SIZE,
                    "max_att": MAX_ATTEMPTS,
                    "default_tid": DEFAULT_TENANT_ID,
                },
            ).fetchall()

            if not rows:
                conn.rollback()
                return 0

            queue_ids = [r[0] for r in rows]
            # items: list of (queue_id, target_type, target_id, tenant_id)
            items: List[Tuple[int, str, int, str]] = [
                (r[0], r[1], r[2], r[3]) for r in rows
            ]

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

        # Step 2: group items by tenant_id so we can set context per group.
        # Within each group we still fetch content and build the text list together
        # before the single GPU encode call, then write back per-tenant.

        # tenant_groups: { tenant_id -> [(queue_id, target_type, target_id), ...] }
        tenant_groups: Dict[str, List[Tuple[int, str, int]]] = defaultdict(list)
        for queue_id, target_type, target_id, t_id in items:
            tenant_groups[t_id].append((queue_id, target_type, target_id))

        # Step 3: fetch content for all items (per tenant so connections are correct)
        # We accumulate a flat list for the single batched encode, but track which
        # (queue_id, target_type, target_id, tenant_id) each index corresponds to.
        texts: List[str] = []
        valid: List[Tuple[int, str, int, str]] = []  # (queue_id, target_type, target_id, tenant_id)

        for t_id, group_items in tenant_groups.items():
            set_tenant_context(t_id)
            for queue_id, target_type, target_id in group_items:
                content = _get_content(target_type, target_id, tenant_id=t_id)
                if content and content.strip():
                    texts.append(content.strip())
                    valid.append((queue_id, target_type, target_id, t_id))
                else:
                    _mark_failed(queue_id, f"{target_type} {target_id}: no content", tenant_id=t_id)

        if not texts:
            return 0

        # Step 4: generate embeddings in one GPU batch (RTX 5090)
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
            for queue_id, _, _, t_id in valid:
                _mark_failed(queue_id, str(exc), tenant_id=t_id)
            return 0

        # Step 5: upsert embeddings and mark done, using tenant-scoped connections.
        # C-3 fix preserved: each item uses its own connection so a per-item failure
        # cannot rollback successfully-written vectors for earlier items in the batch.
        stored = 0
        for i, (queue_id, target_type, target_id, t_id) in enumerate(valid):
            set_tenant_context(t_id)
            vec_list = vectors[i].tolist()
            vec_str = str(vec_list)  # '[0.123, ...]' — stored as TEXT until pgvector installed

            try:
                with tenant_connection(t_id) as conn:
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
                    conn.commit()
                stored += 1
            except Exception as exc:
                logger.error("Failed to store embedding %s/%d: %s", target_type, target_id, exc)
                _mark_failed(queue_id, str(exc), tenant_id=t_id)

        if stored:
            logger.info("Embedded %d/%d items (type distribution: %s)",
                        stored, len(valid),
                        {t: sum(1 for _, tt, _, _ in valid if tt == t) for t in {'memory', 'segment'}})

            # Migration 007: trigger async contradiction + dedup check for newly embedded memories.
            # Group memory_ids by tenant so each Celery task runs with the right tenant context.
            # Runs AFTER embeddings are committed so cosine similarity queries work correctly.
            tenant_memory_ids: Dict[str, List[int]] = defaultdict(list)
            for _, target_type, target_id, t_id in valid:
                if target_type == "memory":
                    tenant_memory_ids[t_id].append(target_id)

            for t_id, memory_ids in tenant_memory_ids.items():
                if memory_ids:
                    try:
                        from ..tasks.pipeline_tasks import check_contradictions_batch
                        check_contradictions_batch.delay(memory_ids, tenant_id=t_id)
                    except Exception as exc:
                        logger.debug("Could not queue check_contradictions_batch: %s", exc)

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

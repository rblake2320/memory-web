"""
Celery tasks for the processing pipeline.
Chains: segment → tag → extract_entities → synthesize_memories → embed

Phase 1 fixes:
  - requeue_stalled: attempt counter stops infinite retry loops after 5 attempts
  - run_full_pipeline: catches OllamaUnavailableError / SynthesisFailedError and
    records stage + segment_id in PipelineRun.error_message

Tenant-aware (Migration 013a):
  - Every task that touches the database accepts tenant_id: str = DEFAULT_TENANT_ID
  - set_tenant_context(tenant_id) is called at the top of each task body
  - engine.connect() replaced with tenant_connection(tenant_id)
  - Cross-task .delay() calls forward tenant_id as a keyword argument
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from celery import chain, group

from ..celery_app import celery_app
from ..database import (
    db_session,
    engine,
    tenant_connection,
    get_tenant_context,
    set_tenant_context,
    DEFAULT_TENANT_ID,
)
from ..models import Conversation, Memory, MemoryLink, PipelineRun, RetentionLog, Source
from ..pipelines import segmenter, tagger, entity_extractor, memory_synthesizer, embedder
from ..pipelines.memory_synthesizer import OllamaUnavailableError, SynthesisFailedError

logger = logging.getLogger(__name__)

MAX_PIPELINE_ATTEMPTS = 5  # watchdog stops retrying after this many failures


def _update_pipeline_run(
    source_id: int,
    stage: str,
    status: str,
    task_id: str = None,
    records: int = 0,
    error: str = None,
) -> None:
    with db_session() as db:
        run = (
            db.query(PipelineRun)
            .filter(PipelineRun.source_id == source_id, PipelineRun.stage == stage)
            .first()
        )
        if not run:
            run = PipelineRun(source_id=source_id, stage=stage)
            db.add(run)

        run.status = status
        run.celery_task_id = task_id
        run.records_processed = records
        if error is not None:
            run.error_message = error
        if status == "running":
            run.started_at = datetime.utcnow()
        elif status in ("done", "failed", "permanently_failed"):
            run.completed_at = datetime.utcnow()


# ---------------------------------------------------------------------------
# Per-conversation pipeline
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="memoryweb.segment_conversation")
def segment_conversation_task(self, conversation_id: int, tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """Heuristic + LLM segmentation of one conversation."""
    set_tenant_context(tenant_id)
    self.update_state(state="STARTED", meta={"conversation_id": conversation_id})
    count = segmenter.segment_conversation(conversation_id, use_llm=True)
    return {"conversation_id": conversation_id, "segments_created": count}


@celery_app.task(bind=True, name="memoryweb.tag_conversation")
def tag_conversation_task(self, conversation_id: int, tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """Tag all segments in a conversation."""
    set_tenant_context(tenant_id)
    count = tagger.tag_conversation_segments(conversation_id)
    return {"conversation_id": conversation_id, "tags_created": count}


@celery_app.task(bind=True, name="memoryweb.extract_entities_conversation")
def extract_entities_conversation_task(self, conversation_id: int, tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """Extract entities from all segments in a conversation."""
    set_tenant_context(tenant_id)
    with db_session() as db:
        from ..models import Segment
        segments = (
            db.query(Segment)
            .filter(Segment.conversation_id == conversation_id)
            .all()
        )
        seg_ids = [s.id for s in segments]

    total = 0
    for seg_id in seg_ids:
        total += entity_extractor.extract_entities_for_segment(seg_id)

    return {"conversation_id": conversation_id, "entity_mentions_created": total}


@celery_app.task(bind=True, name="memoryweb.synthesize_conversation")
def synthesize_conversation_task(self, conversation_id: int, tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """Synthesize memories from all segments in a conversation."""
    set_tenant_context(tenant_id)
    with db_session() as db:
        from ..models import Segment
        segments = (
            db.query(Segment)
            .filter(Segment.conversation_id == conversation_id)
            .all()
        )
        seg_ids = [s.id for s in segments]

    total = 0
    for seg_id in seg_ids:
        total += memory_synthesizer.synthesize_memories_for_segment(seg_id)

    return {"conversation_id": conversation_id, "memories_created": total}


@celery_app.task(bind=True, name="memoryweb.embed_conversation")
def embed_conversation_task(self, conversation_id: int, tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """Embed segments for a conversation (memories are handled by embedding_worker daemon)."""
    set_tenant_context(tenant_id)
    segs = embedder.embed_segments(conversation_id)
    return {
        "conversation_id": conversation_id,
        "segments_embedded": segs,
        "memories_embedded": 0,  # handled by embedding_worker via EmbeddingQueue
    }


# ---------------------------------------------------------------------------
# Full pipeline for one source
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="memoryweb.run_full_pipeline")
def run_full_pipeline(self, source_id: int, tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    Run the complete processing pipeline for all conversations in a source.
    Chains: segment → tag → extract → synthesize → embed per conversation.

    OllamaUnavailableError aborts the entire pipeline (Ollama is down for everyone).
    SynthesisFailedError for a specific segment is recorded and processing continues.
    """
    set_tenant_context(tenant_id)
    # Guard: source_id=-1 (or any sentinel <=0) means ingest produced no real source row.
    # Attempting _update_pipeline_run with source_id=-1 violates the FK on pipeline_runs
    # and crashes the Celery worker, preventing all future task processing.
    if source_id <= 0:
        logger.error(
            "run_full_pipeline called with invalid source_id=%s — skipping. "
            "This usually means ingest_session_file returned a sentinel value.",
            source_id,
        )
        return {"source_id": source_id, "skipped": True, "reason": "invalid_source_id"}
    self.update_state(state="STARTED", meta={"source_id": source_id, "stage": "init"})
    _update_pipeline_run(source_id, "full_pipeline", "running", task_id=self.request.id)

    with db_session() as db:
        convs = (
            db.query(Conversation)
            .filter(Conversation.source_id == source_id)
            .all()
        )
        conv_ids = [c.id for c in convs]

    results = {"source_id": source_id, "conversations": len(conv_ids)}
    total_segs = total_mems = total_ents = 0
    errors = []

    for i, conv_id in enumerate(conv_ids):
        self.update_state(
            state="PROGRESS",
            meta={"source_id": source_id, "progress": i / max(len(conv_ids), 1), "stage": "segment"},
        )
        try:
            segs = segmenter.segment_conversation(conv_id, use_llm=True)
            total_segs += segs

            tagger.tag_conversation_segments(conv_id)

            with db_session() as db:
                from ..models import Segment
                seg_ids = [s.id for s in db.query(Segment).filter(Segment.conversation_id == conv_id).all()]

            for seg_id in seg_ids:
                try:
                    total_ents += entity_extractor.extract_entities_for_segment(seg_id)
                except Exception as e:
                    err_msg = f"entity_extraction conv={conv_id} seg={seg_id}: {e}"
                    logger.warning(err_msg)
                    errors.append(err_msg)

                try:
                    total_mems += memory_synthesizer.synthesize_memories_for_segment(seg_id)
                except OllamaUnavailableError as e:
                    # Ollama is down — abort entire pipeline, record and re-raise
                    err_msg = f"Ollama unavailable at conv={conv_id} seg={seg_id}: {e}"
                    logger.error(err_msg)
                    _update_pipeline_run(source_id, "full_pipeline", "failed", error=err_msg)
                    raise
                except SynthesisFailedError as e:
                    # Synthesis failed for this segment — record and continue
                    err_msg = f"synthesis_failed conv={conv_id} seg={seg_id}: {e}"
                    logger.warning(err_msg)
                    errors.append(err_msg)
                    continue

            embedder.embed_segments(conv_id)

        except OllamaUnavailableError:
            raise  # already recorded above; let Celery mark task as failed
        except Exception as e:
            err_msg = f"pipeline_error conv={conv_id}: {e}"
            logger.error(err_msg)
            errors.append(err_msg)

    results.update({
        "segments": total_segs,
        "memories": total_mems,
        "entity_mentions": total_ents,
        "errors": errors,
    })

    if errors:
        _update_pipeline_run(
            source_id, "full_pipeline", "done",
            records=total_mems,
            error=f"{len(errors)} segment errors: " + "; ".join(errors[:3]),
        )
    else:
        _update_pipeline_run(source_id, "full_pipeline", "done", records=total_mems)

    return results


# ---------------------------------------------------------------------------
# Post-embedding: contradiction detection + near-duplicate write gate
# (Migration 007 + 008: replaces synchronous check in memory_synthesizer)
# ---------------------------------------------------------------------------

@celery_app.task(name="memoryweb.check_contradictions_batch")
def check_contradictions_batch(memory_ids: List[int], tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    Check newly embedded memories for near-duplicates and contradictions.
    Triggered by EmbeddingWorker after a successful batch embed.

    Thresholds (Migration 008):
      cos > 0.92 → near-duplicate → increment corroboration_count on canonical,
                                    set canonical_group_id on the newer memory
      0.85 < cos ≤ 0.92 → similar → ask Ollama: contradiction or related?
        - contradiction confirmed → set valid_until + superseded_by on old memory
        - not contradiction      → create MemoryLink(link_type='related')
    """
    set_tenant_context(tenant_id)

    from ..models import Memory, MemoryLink, _HAS_PGVECTOR
    from ..config import settings as _settings
    from ..services.ollama_client import generate_json
    from ..services.event_log import append_event
    from sqlalchemy import text as _text

    # C-2 fix: _HAS_PGVECTOR is evaluated at models.py import time. If the DB
    # was unreachable when the Celery worker started, the <=> operator is absent
    # and every cosine query fails silently. Guard here so the symptom is a clear
    # log line, not a swallowed exception that reports errors=N with no detail.
    if not _HAS_PGVECTOR:
        logger.warning("check_contradictions_batch: pgvector unavailable — skipping %d ids", len(memory_ids))
        return {"skipped": True, "reason": "pgvector_unavailable"}

    SCHEMA = _settings.MW_DB_SCHEMA
    NEAR_DUP_THRESHOLD = 0.92
    CONTRADICT_THRESHOLD = 0.85

    superseded = 0
    corroborated = 0
    related_linked = 0
    errors = 0

    for new_id in memory_ids:
        try:
            # Fetch the embedding for this memory
            with tenant_connection(tenant_id) as conn:
                emb_row = conn.execute(
                    _text(f"""
                        SELECT vector FROM {SCHEMA}.embeddings
                        WHERE target_type = 'memory' AND target_id = :mid
                    """),
                    {"mid": new_id},
                ).fetchone()

            if not emb_row:
                continue  # embedding not yet ready, skip

            qvec = emb_row[0]

            # Find existing memories with high cosine similarity
            with tenant_connection(tenant_id) as conn:
                rows = conn.execute(
                    _text(f"""
                        SELECT e.target_id, m.fact, m.category,
                               1 - (e.vector <=> CAST(:qvec AS vector)) AS sim
                        FROM {SCHEMA}.embeddings e
                        JOIN {SCHEMA}.memories m ON m.id = e.target_id
                        WHERE e.target_type = 'memory'
                          AND e.target_id != :new_id
                          AND m.tombstoned_at IS NULL
                          AND m.valid_until IS NULL
                          AND 1 - (e.vector <=> CAST(:qvec AS vector)) > :threshold
                        ORDER BY sim DESC
                        LIMIT 10
                    """),
                    {
                        "qvec": str(qvec),
                        "new_id": new_id,
                        "threshold": CONTRADICT_THRESHOLD,
                    },
                ).fetchall()

            for old_id, old_fact, old_cat, sim in rows:
                try:
                    with db_session() as db:
                        new_mem = db.query(Memory).get(new_id)
                        old_mem = db.query(Memory).get(old_id)
                        if not new_mem or not old_mem:
                            continue

                        if float(sim) >= NEAR_DUP_THRESHOLD:
                            # Near-duplicate: merge — increment corroboration on canonical (older)
                            # The canonical is the one with lower ID (created first)
                            canonical = old_mem if old_mem.id < new_mem.id else new_mem
                            duplicate = new_mem if old_mem.id < new_mem.id else old_mem

                            canonical.corroboration_count = (canonical.corroboration_count or 1) + 1
                            if duplicate.canonical_group_id is None:
                                duplicate.canonical_group_id = canonical.id

                            # Link as corroborates
                            existing_link = db.query(MemoryLink).filter(
                                MemoryLink.memory_id_a == canonical.id,
                                MemoryLink.memory_id_b == duplicate.id,
                                MemoryLink.link_type == "corroborates",
                            ).first()
                            if not existing_link:
                                db.add(MemoryLink(
                                    memory_id_a=canonical.id,
                                    memory_id_b=duplicate.id,
                                    link_type="corroborates",
                                    confidence=float(sim),
                                ))
                            db.flush()
                            # Migration 012: recompute canonical confidence now that
                            # corroboration_count increased (more independent roots = higher trust)
                            try:
                                from ..services.memory_integrity import _recompute_memory as _rm
                                _rm(db, canonical.id)
                            except Exception as _ri_err:
                                logger.debug(
                                    "recompute for canonical %d failed (non-fatal): %s",
                                    canonical.id, _ri_err,
                                )
                            corroborated += 1
                            continue

                        # 0.85–0.92 range: ask Ollama for contradiction classification
                        try:
                            result = generate_json(f"""
Do these two facts contradict each other?
Fact A (existing): {old_fact}
Fact B (new): {new_mem.fact}

Return ONLY: {{"contradicts": true}} or {{"contradicts": false}}
""")
                        except Exception:
                            result = {}

                        if isinstance(result, dict) and result.get("contradicts"):
                            # Confirmed contradiction: invalidate old memory
                            if old_mem.valid_until is None:
                                old_mem.valid_until = datetime.utcnow()
                                old_mem.superseded_by = new_mem.id
                                # Migration 012: set belief_state and corrected_at
                                old_mem.belief_state = "superseded"
                                old_mem.corrected_at = datetime.utcnow()
                                # Supersedes link
                                db.add(MemoryLink(
                                    memory_id_a=old_id,
                                    memory_id_b=new_id,
                                    link_type="supersedes",
                                    confidence=float(sim),
                                ))
                                db.flush()
                                # Migration 012: recompute + mark certificates stale
                                try:
                                    from ..services.memory_integrity import (
                                        _recompute_memory as _rm,
                                        mark_certificates_stale as _mcs,
                                    )
                                    _rm(db, old_id)
                                    _mcs([old_id], f"contradicted_by:{new_id}", db)
                                except Exception as _ri_err:
                                    logger.debug(
                                        "recompute/cert_stale for %d failed (non-fatal): %s",
                                        old_id, _ri_err,
                                    )
                                append_event(
                                    "memory_superseded", "memory", old_id,
                                    {"superseded_by": new_id, "sim": float(sim), "old_fact": old_fact[:200]},
                                )
                                logger.info(
                                    "Contradiction: memory %d superseded by %d (sim=%.3f)",
                                    old_id, new_id, sim,
                                )
                                superseded += 1
                        else:
                            # Similar but not contradicting — create 'related' link
                            existing_link = db.query(MemoryLink).filter(
                                MemoryLink.memory_id_a == old_id,
                                MemoryLink.memory_id_b == new_id,
                            ).first()
                            if not existing_link:
                                db.add(MemoryLink(
                                    memory_id_a=old_id,
                                    memory_id_b=new_id,
                                    link_type="related",
                                    confidence=float(sim),
                                ))
                                db.flush()
                            related_linked += 1

                except Exception as e:
                    logger.debug("check_contradictions inner error for %d vs %d: %s", new_id, old_id, e)
                    errors += 1

        except Exception as e:
            logger.warning("check_contradictions_batch error for memory %d: %s", new_id, e)
            errors += 1

    logger.info(
        "check_contradictions_batch: superseded=%d, corroborated=%d, related=%d, errors=%d",
        superseded, corroborated, related_linked, errors,
    )
    return {
        "superseded": superseded,
        "corroborated": corroborated,
        "related_linked": related_linked,
        "errors": errors,
        "processed": len(memory_ids),
    }


# ---------------------------------------------------------------------------
# Sweep: process conversations that never got segmented (safety net)
# ---------------------------------------------------------------------------

@celery_app.task(name="memoryweb.sweep_unprocessed")
def sweep_unprocessed(tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    Find conversations with no segments and re-trigger the pipeline for their sources.
    Runs every 15 minutes via Celery Beat.
    """
    set_tenant_context(tenant_id)

    from sqlalchemy import text as _text
    from ..config import settings as _settings

    with tenant_connection(tenant_id) as conn:
        rows = conn.execute(_text(f"""
            SELECT DISTINCT c.source_id
            FROM {_settings.MW_DB_SCHEMA}.conversations c
            LEFT JOIN {_settings.MW_DB_SCHEMA}.segments s ON s.conversation_id = c.id
            WHERE s.id IS NULL
              AND c.source_id IS NOT NULL
        """)).fetchall()

    source_ids = [r[0] for r in rows]
    for sid in source_ids:
        run_full_pipeline.delay(sid, tenant_id=tenant_id)

    logger.info("sweep_unprocessed: queued %d sources for pipeline", len(source_ids))
    return {"sources_queued": len(source_ids), "source_ids": source_ids}


# ---------------------------------------------------------------------------
# Watchdog: requeue stalled pipeline and embedding jobs (Celery Beat task)
# ---------------------------------------------------------------------------

@celery_app.task(name="memoryweb.requeue_stalled")
def requeue_stalled(tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    Self-healing watchdog — runs every 10 minutes via Celery Beat.

    1. Pipeline runs stuck in 'running' for > 1 hour → mark failed
    2. Pipeline runs with status='failed' AND attempts < MAX_PIPELINE_ATTEMPTS → re-trigger
       After MAX_PIPELINE_ATTEMPTS failures → mark permanently_failed (no more retries)
    3. embedding_queue items with status='failed' and attempts < MAX_ATTEMPTS → reset to pending

    Phase 1 fix: attempt counter prevents infinite retry storms when Ollama is down.
    """
    set_tenant_context(tenant_id)

    stale_cutoff = datetime.utcnow() - timedelta(hours=1)
    requeued_pipelines = 0
    permanently_failed_pipelines = 0
    reset_embeddings = 0
    marked_failed = 0

    with db_session() as db:
        # Step 1: mark stale running pipeline_runs as failed
        stale_runs = (
            db.query(PipelineRun)
            .filter(
                PipelineRun.status == "running",
                PipelineRun.started_at < stale_cutoff,
            )
            .all()
        )
        for run in stale_runs:
            run.status = "failed"
            run.error_message = "Watchdog: timed out after >1h"
            run.completed_at = datetime.utcnow()
            marked_failed += 1

        # Step 2: re-trigger failed pipeline_runs with attempt backoff
        failed_runs = (
            db.query(PipelineRun)
            .filter(
                PipelineRun.status == "failed",
                PipelineRun.source_id.isnot(None),
            )
            .order_by(PipelineRun.source_id, PipelineRun.attempts.desc())
            .distinct(PipelineRun.source_id)
            .all()
        )
        for run in failed_runs:
            if not run.source_id:
                continue
            current_attempts = run.attempts or 0
            if current_attempts >= MAX_PIPELINE_ATTEMPTS:
                # Already at max — mark permanently failed if not already
                if run.status != "permanently_failed":
                    run.status = "permanently_failed"
                    run.error_message = (
                        (run.error_message or "")
                        + f" | Watchdog: gave up after {current_attempts} attempts"
                    )
                    run.completed_at = datetime.utcnow()
                    permanently_failed_pipelines += 1
            else:
                run.attempts = current_attempts + 1
                run_full_pipeline.delay(run.source_id, tenant_id=tenant_id)
                requeued_pipelines += 1

    # Step 3: reset failed embedding_queue items to pending if retries remain
    from sqlalchemy import text as _text
    from ..config import settings as _settings
    MAX_ATT = 3

    with tenant_connection(tenant_id) as conn:
        result = conn.execute(
            _text(f"""
                UPDATE {_settings.MW_DB_SCHEMA}.embedding_queue
                SET status = 'pending',
                    error = NULL,
                    started_at = NULL
                WHERE status = 'failed'
                  AND attempts < :max_att
            """),
            {"max_att": MAX_ATT},
        )
        reset_embeddings = result.rowcount
        conn.commit()

    logger.info(
        "requeue_stalled: marked_failed=%d, requeued=%d, permanently_failed=%d, reset_embeddings=%d",
        marked_failed, requeued_pipelines, permanently_failed_pipelines, reset_embeddings,
    )
    return {
        "marked_failed": marked_failed,
        "requeued_pipelines": requeued_pipelines,
        "permanently_failed_pipelines": permanently_failed_pipelines,
        "reset_embeddings": reset_embeddings,
    }


# ---------------------------------------------------------------------------
# Time-decay sweep: tombstone old, low-utility, low-importance memories
# ---------------------------------------------------------------------------

@celery_app.task(name="memoryweb.memory_decay_sweep")
def memory_decay_sweep(tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    Daily sweep that soft-deletes memories which have become stale:
    - belief_state = 'active'
    - importance <= MW_DECAY_MAX_IMPORTANCE (default 2)
    - utility_score < MW_DECAY_MIN_UTILITY (default 0.1)
    - last_accessed_at older than MW_DECAY_MIN_AGE_DAYS (default 90) days

    Configure via env vars. Set MW_DECAY_MIN_AGE_DAYS=0 to disable effectively.
    """
    set_tenant_context(tenant_id)

    min_age_days = int(os.environ.get("MW_DECAY_MIN_AGE_DAYS", "90"))
    min_utility = float(os.environ.get("MW_DECAY_MIN_UTILITY", "0.1"))
    max_importance = int(os.environ.get("MW_DECAY_MAX_IMPORTANCE", "2"))

    cutoff = datetime.utcnow() - timedelta(days=min_age_days)
    tombstoned = 0

    with db_session() as db:
        candidates = (
            db.query(Memory)
            .filter(
                Memory.tombstoned_at.is_(None),
                Memory.belief_state == "active",
                Memory.importance <= max_importance,
                Memory.utility_score < min_utility,
                Memory.last_accessed_at < cutoff,
            )
            .all()
        )

        ids = [m.id for m in candidates]
        now = datetime.utcnow()

        for m in candidates:
            m.tombstoned_at = now
            tombstoned += 1

        if ids:
            db.add(RetentionLog(
                action="tombstone",
                target_type="memory",
                target_ids=ids,
                reason="time_decay_sweep",
                triggered_by="celery_beat",
            ))

    logger.info(
        "memory_decay_sweep: tombstoned=%d (age>%dd, utility<%.2f, importance<=%d)",
        tombstoned, min_age_days, min_utility, max_importance,
    )
    return {
        "tombstoned": tombstoned,
        "cutoff_date": cutoff.isoformat(),
        "min_utility": min_utility,
        "max_importance": max_importance,
    }


# ---------------------------------------------------------------------------
# Semantic dedup audit: find near-duplicate memories not yet linked
# ---------------------------------------------------------------------------

@celery_app.task(name="memoryweb.semantic_dedup_audit")
def semantic_dedup_audit(tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    On-demand audit that finds memory pairs with cosine similarity > 0.92
    that are not yet linked in memory_links, and creates corroboration links.

    Dispatched by POST /api/maintain/dedup-audit. Processes up to 500 pairs
    per run (call again to continue if many are found).
    """
    set_tenant_context(tenant_id)

    from sqlalchemy import text as _text
    from ..config import settings as _settings

    SCHEMA = _settings.MW_DB_SCHEMA
    COSINE_DIST_THRESHOLD = 0.08  # cosine distance < 0.08 → similarity > 0.92

    new_links = 0
    pairs_scanned = 0

    with tenant_connection(tenant_id) as conn:
        rows = conn.execute(_text(f"""
            SELECT e1.target_id AS mem_a,
                   e2.target_id AS mem_b,
                   1.0 - (e1.vector <=> e2.vector) AS similarity
            FROM {SCHEMA}.embeddings e1
            JOIN {SCHEMA}.embeddings e2
              ON e2.target_id > e1.target_id
             AND e2.target_type = 'memory'
            JOIN {SCHEMA}.memories m1
              ON m1.id = e1.target_id AND m1.tombstoned_at IS NULL
            JOIN {SCHEMA}.memories m2
              ON m2.id = e2.target_id AND m2.tombstoned_at IS NULL
            WHERE e1.target_type = 'memory'
              AND e1.vector <=> e2.vector < :threshold
              AND NOT EXISTS (
                  SELECT 1 FROM {SCHEMA}.memory_links ml
                  WHERE (ml.memory_id_a = e1.target_id AND ml.memory_id_b = e2.target_id)
                     OR (ml.memory_id_a = e2.target_id AND ml.memory_id_b = e1.target_id)
              )
            LIMIT 500
        """), {"threshold": COSINE_DIST_THRESHOLD}).fetchall()

        pairs_scanned = len(rows)

        for row in rows:
            mem_a, mem_b, similarity = row[0], row[1], float(row[2])
            conn.execute(_text(f"""
                INSERT INTO {SCHEMA}.memory_links (memory_id_a, memory_id_b, link_type, confidence)
                VALUES (:a, :b, 'corroborates', :conf)
                ON CONFLICT DO NOTHING
            """), {"a": mem_a, "b": mem_b, "conf": round(similarity, 4)})
            new_links += 1

        conn.commit()

    logger.info(
        "semantic_dedup_audit: pairs_scanned=%d, new_links=%d",
        pairs_scanned, new_links,
    )
    return {
        "pairs_scanned": pairs_scanned,
        "new_links_created": new_links,
        "threshold": 1.0 - COSINE_DIST_THRESHOLD,
    }

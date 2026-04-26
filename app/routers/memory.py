"""Memory, conversation, and segment API routes."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from ..database import db_session, engine
from ..deps import get_db
from ..models import (
    AnswerCertificate, Conversation, Embedding, EmbeddingQueue,
    Memory, MemoryLink, MemoryProvenance, Message, RetentionLog, Segment, Source, Tag,
)
from ..schemas import (
    AnswerCertificateOut,
    CertificateListResponse,
    ConversationOut,
    EventLogOut,
    EventLogVerifyOut,
    MemoryHistoryOut,
    MemoryListResponse,
    MemoryOut,
    MemoryWithProvenance,
    MessageOut,
    ProvenanceChain,
    SegmentOut,
    SourceInvalidateRequest,
    SourceInvalidateResult,
    SourceOut,
)
from ..services.retrieval import _build_provenance
from ..services.event_log import append_event, verify_chain, get_memory_history
from ..services.memory_integrity import (
    _recompute_memory,
    mark_certificates_stale,
    clear_stale_certificates,
    get_certificate,
    list_certificates,
)

router = APIRouter(prefix="/api", tags=["memory"])


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------

@router.get("/memories", response_model=MemoryListResponse)
def list_memories(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, le=200),
    category: Optional[str] = None,
    min_importance: Optional[int] = None,
    include_tombstoned: bool = False,
    db: Session = Depends(get_db),
):
    """List memories with pagination and optional filters."""
    q = db.query(Memory)
    if not include_tombstoned:
        q = q.filter(Memory.tombstoned_at.is_(None))
    if category:
        q = q.filter(Memory.category == category)
    if min_importance:
        q = q.filter(Memory.importance >= min_importance)

    total = q.count()
    items = q.order_by(Memory.importance.desc(), Memory.created_at.desc())\
             .offset((page - 1) * page_size)\
             .limit(page_size)\
             .all()

    return MemoryListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[MemoryOut.model_validate(m) for m in items],
    )


@router.get("/memories/{memory_id}", response_model=MemoryWithProvenance)
def get_memory(memory_id: int, db: Session = Depends(get_db)):
    """Get a single memory with full provenance chain."""
    mem = db.query(Memory).get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Update access counts, then refresh to get committed scalar values
    mem.access_count = (mem.access_count or 0) + 1
    mem.last_accessed_at = datetime.utcnow()
    db.commit()
    db.refresh(mem)  # reload scalar fields after commit (avoids expired-instance issues)

    provenance = _build_provenance(memory_id, db)

    # Build response from MemoryOut (scalar fields only) then attach provenance.
    # Do NOT use model_validate(mem) on MemoryWithProvenance directly — that would
    # try to validate mem.provenance (ORM relationship of MemoryProvenance objects)
    # against ProvenanceChain, which has no from_attributes=True.
    mem_out = MemoryOut.model_validate(mem)
    return MemoryWithProvenance(**mem_out.model_dump(), provenance=provenance)


@router.get("/memories/{memory_id}/provenance", response_model=List[ProvenanceChain])
def get_memory_provenance(memory_id: int, db: Session = Depends(get_db)):
    """Get full provenance chain for a memory."""
    mem = db.query(Memory).get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _build_provenance(memory_id, db)


@router.post("/memories/{memory_id}/helpful", response_model=MemoryOut)
def mark_memory_helpful(memory_id: int, db: Session = Depends(get_db)):
    """
    Signal that this memory was helpful. Increments helpful_count and
    recalculates utility_score so genuinely useful facts float to the top.
    """
    mem = db.query(Memory).get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    mem.helpful_count = (mem.helpful_count or 0) + 1
    rc = max(mem.retrieval_count or 1, mem.helpful_count)
    hc = mem.helpful_count
    imp_score = ((mem.importance or 3) - 1) / 4.0
    # Cold-start: importance dominates until retrieval data accumulates
    if rc <= 5:
        mem.utility_score = round(0.3 * (hc + 1) / (rc + 2) + 0.7 * imp_score, 4)
    else:
        mem.utility_score = round(0.7 * (hc + 1) / (rc + 2) + 0.3 * imp_score, 4)
    db.commit()
    db.refresh(mem)
    return MemoryOut.model_validate(mem)


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db)):
    """
    Soft-delete (tombstone) a memory. Reversible within 30 seconds via
    POST /api/retain/restore/memory/{id}. Logged to retention_log.
    """
    mem = db.query(Memory).get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    if mem.tombstoned_at is not None:
        raise HTTPException(status_code=410, detail="Memory already deleted")

    fact_preview = (mem.fact or "")[:200]
    mem.tombstoned_at = datetime.utcnow()

    log = RetentionLog(
        action="user_delete",
        target_type="memory",
        target_ids={"ids": [memory_id], "fact": fact_preview},
        reason=f"Dashboard delete: {fact_preview}",
        triggered_by="dashboard",
    )
    db.add(log)
    db.commit()
    return {"tombstoned": memory_id, "fact_preview": fact_preview[:80]}


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, hard: bool = False, db: Session = Depends(get_db)):
    """
    Delete a source and cascade to all its conversations/messages/segments/memories.
    Set hard=true for immediate purge; without it, tombstones all derived records.
    """
    src = db.query(Source).get(source_id)
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")

    if hard:
        # Cascade via FK ON DELETE CASCADE — just delete the source row
        db.delete(src)
        db.commit()
        return {"deleted_source": source_id, "mode": "hard"}

    # Soft-delete: tombstone all messages in conversations from this source
    now = datetime.utcnow()
    convs = db.query(Conversation).filter(Conversation.source_id == source_id).all()
    msg_count = seg_count = mem_count = 0

    for conv in convs:
        for msg in conv.messages:
            if not msg.tombstoned_at:
                msg.tombstoned_at = now
                msg_count += 1
        for seg in conv.segments:
            if not seg.tombstoned_at:
                seg.tombstoned_at = now
                seg_count += 1
                # Tombstone memories whose only provenance is this segment
                for prov in seg.provenance_links:
                    mem = db.query(Memory).get(prov.memory_id)
                    if mem and not mem.tombstoned_at:
                        # Only tombstone if all provenance is tombstoned
                        all_provs = db.query(MemoryProvenance).filter(
                            MemoryProvenance.memory_id == mem.id
                        ).all()
                        all_tombstoned = all(
                            (p.segment_id is None or
                             db.query(Segment).get(p.segment_id) is None or
                             db.query(Segment).get(p.segment_id).tombstoned_at is not None)
                            for p in all_provs
                        )
                        if all_tombstoned:
                            mem.tombstoned_at = now
                            mem_count += 1

    db.commit()
    return {
        "tombstoned_source": source_id,
        "mode": "soft",
        "messages": msg_count,
        "segments": seg_count,
        "memories": mem_count,
    }


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@router.get("/conversations", response_model=List[ConversationOut])
def list_conversations(
    source_id: Optional[int] = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List conversations."""
    q = db.query(Conversation)
    if source_id:
        q = q.filter(Conversation.source_id == source_id)
    convs = q.order_by(Conversation.started_at.desc()).offset(offset).limit(limit).all()
    return [ConversationOut.model_validate(c) for c in convs]


@router.get("/conversations/{conversation_id}/segments", response_model=List[SegmentOut])
def get_conversation_segments(
    conversation_id: int,
    include_tombstoned: bool = False,
    db: Session = Depends(get_db),
):
    """Get all segments for a conversation."""
    conv = db.query(Conversation).get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    q = db.query(Segment).filter(Segment.conversation_id == conversation_id)
    if not include_tombstoned:
        q = q.filter(Segment.tombstoned_at.is_(None))
    segments = q.order_by(Segment.start_ordinal).all()

    result = []
    for seg in segments:
        tags = [{"axis": t.axis.axis_name if t.axis else "", "value": t.value, "confidence": t.confidence} for t in seg.tags]
        so = SegmentOut.model_validate(seg)
        so.tags = tags
        result.append(so)
    return result


@router.get("/segments/{segment_id}/messages", response_model=List[MessageOut])
def get_segment_messages(
    segment_id: int,
    include_tombstoned: bool = False,
    db: Session = Depends(get_db),
):
    """Get all messages in a segment."""
    seg = db.query(Segment).get(segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Segment not found")

    q = db.query(Message).filter(
        Message.conversation_id == seg.conversation_id,
        Message.ordinal >= seg.start_ordinal,
        Message.ordinal <= seg.end_ordinal,
    )
    if not include_tombstoned:
        q = q.filter(Message.tombstoned_at.is_(None))

    messages = q.order_by(Message.ordinal).all()
    return [MessageOut.model_validate(m) for m in messages]


# ---------------------------------------------------------------------------
# Memory lifecycle history (Migration 010 event log)
# ---------------------------------------------------------------------------

@router.get("/memories/{memory_id}/history", response_model=MemoryHistoryOut)
def get_memory_history_endpoint(memory_id: int, db: Session = Depends(get_db)):
    """
    Return the full event history for a memory from the append-only event log.
    Shows: memory_created → corroborated → superseded → confidence_changed lifecycle.
    Answers: "what did the system believe about this memory on date X?"
    """
    mem = db.query(Memory).get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    events_raw = get_memory_history(memory_id)
    events = [EventLogOut(**e) for e in events_raw]
    return MemoryHistoryOut(memory_id=memory_id, events=events, total=len(events))


@router.get("/event_log/verify", response_model=EventLogVerifyOut)
def verify_event_log():
    """
    Walk the entire event log hash chain and verify integrity.
    Returns {valid, chain_length, first_broken_at}.
    Use this to detect tampering or corruption of the immutable event ledger.
    """
    result = verify_chain()
    return EventLogVerifyOut(**result)


# ---------------------------------------------------------------------------
# Source trust + cascade invalidation (Migration 009)
# ---------------------------------------------------------------------------

@router.post("/sources/{source_id}/invalidate", response_model=SourceInvalidateResult)
def invalidate_source(
    source_id: int,
    body: SourceInvalidateRequest,
    db: Session = Depends(get_db),
):
    """
    Mark a source as retroactively wrong. Cascades to all derived memories:
    - Reduces confidence by 50% (stores original in pre_invalidation_confidence)
    - Demotes derivation_tier to max(current, 5)
    - Does NOT delete or set valid_until — memories remain visible but downranked.
    Reversible via POST /api/sources/{id}/restore.
    """
    src = db.query(Source).get(source_id)
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    if src.invalidated_at is not None:
        raise HTTPException(status_code=409, detail="Source already invalidated")

    now = datetime.utcnow()
    src.invalidated_at = now
    src.invalidation_reason = body.reason or "Manual invalidation"

    # Cascade: demote all derived memories via deterministic recompute
    affected = db.query(Memory).filter(
        Memory.source_id == source_id,
        Memory.tombstoned_at.is_(None),
    ).all()
    affected_ids = [mem.id for mem in affected]

    for mem in affected:
        # Snapshot the current confidence for restore (legacy path — pre_invalidation_confidence
        # is still used by restore to know what value to return to)
        if mem.pre_invalidation_confidence is None:
            mem.pre_invalidation_confidence = mem.confidence
        # Demote trust tier (legacy — recompute will recalculate confidence correctly)
        mem.derivation_tier = max(mem.derivation_tier or 4, 5)

    db.flush()

    # Migration 012: recompute confidence from stable base_trust for each affected memory
    # (replaces blanket 50% cut — deterministic from base, not from mutated value)
    for mem_id in affected_ids:
        try:
            _recompute_memory(db, mem_id)
        except Exception as e:
            logger.debug("recompute_memory failed for %d during invalidation: %s", mem_id, e)

    db.commit()

    # Migration 012: mark all certificates that used these memories as stale
    if affected_ids:
        try:
            mark_certificates_stale(affected_ids, f"source_invalidated:{source_id}", db)
            db.commit()
        except Exception as e:
            logger.debug("mark_certificates_stale failed (non-fatal): %s", e)

    append_event(
        "source_invalidated", "source", source_id,
        {"reason": src.invalidation_reason, "affected_count": len(affected_ids)},
    )

    return SourceInvalidateResult(
        source_id=source_id,
        action="invalidated",
        affected_memories=len(affected_ids),
    )


@router.post("/sources/{source_id}/restore", response_model=SourceInvalidateResult)
def restore_source(
    source_id: int,
    db: Session = Depends(get_db),
):
    """
    Restore a previously invalidated source and undo confidence reduction on derived memories.
    Idempotent: safe to call multiple times.
    """
    src = db.query(Source).get(source_id)
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    if src.invalidated_at is None:
        raise HTTPException(status_code=409, detail="Source is not invalidated")

    src.invalidated_at = None
    src.invalidation_reason = None

    # Restore derived memories — undo demotions and recompute from stable base_trust
    affected = db.query(Memory).filter(
        Memory.source_id == source_id,
        Memory.tombstoned_at.is_(None),
    ).all()
    affected_ids = [mem.id for mem in affected]

    for mem in affected:
        # Restore derivation_tier: can't fully reconstruct original, clamp to 4
        if (mem.derivation_tier or 4) >= 5:
            mem.derivation_tier = 4
        # Clear the invalidation snapshot (no longer needed)
        mem.pre_invalidation_confidence = None

    db.flush()

    # Migration 012: recompute from stable base_trust now that source is valid again
    # This is the determinism guarantee: invalidate → restore → confidence is identical
    for mem_id in affected_ids:
        try:
            _recompute_memory(db, mem_id)
        except Exception as e:
            logger.debug("recompute_memory failed for %d during restore: %s", mem_id, e)

    db.commit()

    # Migration 012: conservatively clear stale certificates for recovered memories
    if affected_ids:
        try:
            clear_stale_certificates(affected_ids, db)
            db.commit()
        except Exception as e:
            logger.debug("clear_stale_certificates failed (non-fatal): %s", e)

    append_event(
        "source_restored", "source", source_id,
        {"restored_count": len(affected_ids)},
    )

    return SourceInvalidateResult(
        source_id=source_id,
        action="restored",
        affected_memories=len(affected_ids),
    )


# ---------------------------------------------------------------------------
# Answer certificates (Migration 012)
# ---------------------------------------------------------------------------

@router.get("/certificates", response_model=CertificateListResponse)
def list_answer_certificates(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    stale_only: bool = False,
    db: Session = Depends(get_db),
):
    """
    List answer certificates: a record of what memories and sources backed
    each query response. Use stale_only=true to see certificates that used
    memories/sources that have since been invalidated.
    """
    result = list_certificates(db, limit=limit, offset=offset, stale_only=stale_only)
    items = [AnswerCertificateOut(**item) for item in result["items"]]
    return CertificateListResponse(total=result["total"], items=items)


@router.get("/certificates/{certificate_id}", response_model=AnswerCertificateOut)
def get_answer_certificate(certificate_id: int, db: Session = Depends(get_db)):
    """
    Get a single answer certificate with its full memory lineage (memory_ids)
    and source lineage (source_ids). These are stored in separate junction tables
    so both lineages can be queried independently.
    """
    cert_dict = get_certificate(certificate_id, db)
    if not cert_dict:
        raise HTTPException(status_code=404, detail="Certificate not found")
    return AnswerCertificateOut(**cert_dict)

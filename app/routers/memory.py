"""Memory, conversation, and segment API routes."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import db_session
from ..deps import get_db
from ..models import Conversation, Embedding, EmbeddingQueue, Memory, MemoryLink, MemoryProvenance, Message, RetentionLog, Segment, Source, Tag
from ..schemas import (
    ConversationOut,
    MemoryListResponse,
    MemoryOut,
    MemoryWithProvenance,
    MessageOut,
    ProvenanceChain,
    SegmentOut,
)
from ..services.retrieval import _build_provenance

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

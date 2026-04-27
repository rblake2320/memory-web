"""Retention control API routes."""

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel
from typing import Literal, Optional

from ..database import db_session
from ..models import RetentionLog
from ..schemas import PurgeRequest, RetentionResult
from ..services import retention as ret_svc

router = APIRouter(prefix="/api/retain", tags=["retention"])


class RetentionBody(BaseModel):
    reason: Optional[str] = None


@router.delete("/day/{date}", summary="Tombstone all data for a date (YYYY-MM-DD)")
def tombstone_day(date: str, body: RetentionBody = Body(default=RetentionBody())):
    return ret_svc.tombstone_by_date(date, reason=body.reason)


@router.delete("/domain/{domain}", summary="Tombstone all segments tagged with domain")
def tombstone_domain(domain: str, body: RetentionBody = Body(default=RetentionBody())):
    return ret_svc.tombstone_by_domain(domain, reason=body.reason)


@router.delete("/conversation/{conversation_id}", summary="Tombstone entire conversation")
def tombstone_conversation(
    conversation_id: int,
    body: RetentionBody = Body(default=RetentionBody()),
):
    return ret_svc.tombstone_conversation(conversation_id, reason=body.reason)


@router.get("/tombstoned", summary="Count tombstoned records")
def list_tombstoned():
    return ret_svc.list_tombstoned()


@router.post("/restore/{target_type}/{target_id}", summary="Restore (un-tombstone) a record")
def restore(target_type: str, target_id: int):
    return ret_svc.restore(target_type, target_id)


@router.get("/log", summary="Recent retention log entries")
def get_retention_log(limit: int = Query(default=50, le=200)):
    """Return recent entries from the retention_log table, newest first."""
    with db_session() as db:
        entries = (
            db.query(RetentionLog)
            .order_by(RetentionLog.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": e.id,
                "action": e.action,
                "target_type": e.target_type,
                "target_ids": e.target_ids,
                "reason": e.reason,
                "triggered_by": e.triggered_by,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ]


@router.post("/purge", summary="Hard-delete old tombstoned records")
def purge(body: PurgeRequest):
    return ret_svc.purge_tombstoned(
        older_than_days=body.older_than_days,
        dry_run=body.dry_run,
    )


class ConflictResolutionBody(BaseModel):
    action: Literal["accept", "reject", "supersede"]
    replacement_id: Optional[int] = None  # required when action="supersede"
    reason: Optional[str] = None


@router.post(
    "/resolve-contradiction/{memory_id}",
    summary="Manually resolve a disputed or quarantined memory",
)
def resolve_contradiction(memory_id: int, body: ConflictResolutionBody):
    """
    Manual override for disputed/quarantined memories.

    - **accept**: Set belief_state back to 'active', restore confidence from base_trust.
    - **reject**: Tombstone the memory permanently.
    - **supersede**: Tombstone and point to replacement_id as the canonical fact.
      Requires replacement_id.
    """
    from datetime import datetime as _dt
    from ..models import Memory

    if body.action == "supersede" and not body.replacement_id:
        raise HTTPException(status_code=422, detail="replacement_id required when action='supersede'")

    with db_session() as db:
        mem = db.query(Memory).filter(Memory.id == memory_id).first()
        if not mem:
            raise HTTPException(status_code=404, detail=f"Memory #{memory_id} not found")

        now = _dt.utcnow()

        if body.action == "accept":
            mem.tombstoned_at = None
            mem.belief_state = "active"
            mem.confidence = mem.base_trust or mem.confidence
            result = {"action": "accepted", "memory_id": memory_id, "belief_state": "active"}

        elif body.action == "reject":
            mem.tombstoned_at = now
            mem.belief_state = "superseded"
            result = {"action": "rejected", "memory_id": memory_id, "tombstoned_at": now.isoformat()}

        elif body.action == "supersede":
            replacement = db.query(Memory).filter(Memory.id == body.replacement_id).first()
            if not replacement:
                raise HTTPException(status_code=404,
                                    detail=f"Replacement memory #{body.replacement_id} not found")
            mem.tombstoned_at = now
            mem.valid_until = now
            mem.superseded_by = body.replacement_id
            mem.belief_state = "superseded"
            result = {
                "action": "superseded",
                "memory_id": memory_id,
                "superseded_by": body.replacement_id,
                "tombstoned_at": now.isoformat(),
            }

        db.add(RetentionLog(
            action="resolve_contradiction",
            target_type="memory",
            target_ids=[memory_id],
            reason=body.reason or f"manual_override:{body.action}",
            triggered_by="api",
        ))

    return result

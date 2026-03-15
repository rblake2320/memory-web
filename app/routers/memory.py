"""Memory router."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app import deps
from app.schemas import MemoryOut, MemoriesResponse
from app.services.memory import get_memories

router = APIRouter()

@router.get("/api/memories", response_model=MemoriesResponse)
async def read_memories(
    db: Session = Depends(deps.get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
):
    """Read memories."""
    offset = (page - 1) * per_page
    memories, total_count = get_memories(db, offset, per_page)
    return {
        "memories": memories,
        "page": page,
        "per_page": per_page,
        "total": total_count,
    }

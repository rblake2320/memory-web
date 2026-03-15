from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from ..database import db_session
from ..deps import get_db
from ..models import Memory, MemoryTag
from ..schemas import MemoryListResponse, MemoryTagRequest, MemoryTagResponse

router = APIRouter(prefix="/api", tags=["memory"])

# existing code...

@router.post("/memories/{memory_id}/tags", response_model=MemoryTagResponse)
def add_tags_to_memory(memory_id: int, tags: MemoryTagRequest, db: Session = Depends(get_db)):
    """Add tags to a memory."""
    memory = db.query(Memory).get(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    
    for tag in tags.tags:
        memory_tag = MemoryTag(memory_id=memory_id, tag=tag)
        db.add(memory_tag)
    
    db.commit()
    return {"tag": tag, "count": len(tags.tags)}

@router.get("/tags", response_model=List[MemoryTagResponse])
def get_all_tags(db: Session = Depends(get_db)):
    """Get all unique tags with counts."""
    tags = db.query(MemoryTag.tag, MemoryTag.memory_id).group_by(MemoryTag.tag).all()
    return [{"tag": tag[0], "count": len([t[1] for t in tags if t[0] == tag[0]])} for tag in set([t[0] for t in tags])]

@router.get("/memories", response_model=MemoryListResponse)
def list_memories(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, le=200),
    category: Optional[str] = None,
    min_importance: Optional[int] = None,
    include_tombstoned: bool = False,
    tag: Optional[str] = None,
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
    if tag:
        q = q.join(MemoryTag, Memory.id == MemoryTag.memory_id).filter(MemoryTag.tag == tag)
    
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


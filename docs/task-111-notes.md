# Task #111: Add date range filter to GET /api/memories in memory-web

from sqlalchemy import and_

@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, le=200),
    category: Optional[str] = None,
    min_importance: Optional[int] = None,
    after: Optional[str] = None,
    before: Optional[str] = None,
    include_tombstoned: bool = False,
    db: Session = Depends(get_db),
):
    """List memories with pagination and optional filters."""
    
    query = db.query(Memory)
    if not include_tombstoned:
        query = query.filter(Memory.tombstoned_at.is_(None))
    if after:
        query = query.filter(Memory.created_at > after)
    if before:
        query = query.filter(Memory.created_at < before)
    if category:
        query = query.filter(Memory.category == category)
    if min_importance:
        query = query.filter(Memory.importance >= min_importance)

    total = query.count()
    items = query.order_by(Memory.importance.desc(), Memory.created_at.desc())\
             .offset((page - 1) * page_size)\
             .limit(page_size)\
             .all()

    return MemoryListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[MemoryOut.model_validate(m) for m in items],
    )

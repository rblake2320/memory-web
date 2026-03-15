"""Memory service."""
from sqlalchemy.orm import Session
from app.models import Memory
from app.schemas import MemoryOut

def get_memories(db: Session, offset: int, limit: int):
    """Get memories."""
    query = db.query(Memory)
    total_count = query.count()
    query = query.offset(offset).limit(limit)
    memories = query.all()
    return [MemoryOut.from_orm(m) for m in memories], total_count

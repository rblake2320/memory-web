"""Memory router for the MemoryWeb API."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from app.database import get_db
from app.models import Memory
from app.schemas import MemoryOut
from sqlalchemy.orm import Session

router = APIRouter()

@router.get("/api/memories", response_model=dict)
def read_memories(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1),
):
    """Read memories with pagination."""
    offset = (page - 1) * per_page
    total_count = db.query(Memory).count()
    memories = (
        db.query(Memory)
        .order_by(Memory.id.desc())
        .offset(offset)
        .limit(per_page)
        .all()
    )
    return {
        "memories": [MemoryOut.from_orm(memory) for memory in memories],
        "page": page,
        "per_page": per_page,
        "total": total_count,
    }

---FILE: app/schemas.py---
"""Schemas for the MemoryWeb API."""
from pydantic import BaseModel
from typing import List

class MemoryOut(BaseModel):
    id: int
    fact: str
    category: str | None
    confidence: float | None
    importance: int | None
    access_count: int
    created_at: str | None
    tombstoned_at: str | None
    provenance: List[dict]

    class Config:
        orm_mode = True

---FILE: app/database.py---
"""Database setup for the MemoryWeb app."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from app.config import settings

SQLALCHEMY_DATABASE_URL = settings.MW_DATABASE_URL

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


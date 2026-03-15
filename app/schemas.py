"""Schemas."""
from pydantic import BaseModel
from typing import List

class MemoryOut(BaseModel):
    id: int
    fact: str
    category: str | None
    confidence: float | None
    importance: float | None
    tombstoned_at: str | None
    access_count: int
    created_at: str | None

    class Config:
        orm_mode = True

class MemoriesResponse(BaseModel):
    memories: List[MemoryOut]
    page: int
    per_page: int
    total: int

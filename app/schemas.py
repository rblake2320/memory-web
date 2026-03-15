from typing import List, Optional
from pydantic import BaseModel

class MemoryTagResponse(BaseModel):
    tag: str
    count: int

class MemoryTagRequest(BaseModel):
    tags: List[str]

class MemoryListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[MemoryOut]
    tags: Optional[List[str]] = None


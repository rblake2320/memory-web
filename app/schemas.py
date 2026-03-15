```python
from pydantic import BaseModel
from typing import List, Optional

class MemorySearchRequest(BaseModel):
    query: str

class MemorySearchResponse(BaseModel):
    results: List[dict]

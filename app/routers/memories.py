```python
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from typing import Optional
from datetime import datetime
import json

from app.database import get_db
from app.schemas import MemorySearchRequest, MemorySearchResponse
from app.models import Memory

router = APIRouter(
    prefix="/api/memories",
    tags=["memories"],
)

@router.post("/search")
async def search_memories(
    request: MemorySearchRequest,
    db = Depends(get_db),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None)
):
    try:
        # Parse date range filter
        if date_from:
            date_from = datetime.fromisoformat(date_from)
        if date_to:
            date_to = datetime.fromisoformat(date_to)

        # Filter memories by date range
        query = db.query(Memory)
        if date_from:
            query = query.filter(Memory.created_at >= date_from)
        if date_to:
            query = query.filter(Memory.created_at <= date_to)

        # Apply search query
        query = query.filter(Memory.text.like(f"%{request.query}%"))

        # Execute query and return results
        results = query.all()
        response = MemorySearchResponse(results=[jsonable_encoder(result) for result in results])
        return JSONResponse(content=json.dumps(response.dict()), media_type="application/json")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

"""Memory API routes."""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from ..deps import get_db
from ..models import Memory
from ..schemas import MemorySearchRequest, MemorySearchResponse
from ..services.ingestion import embed_text

router = APIRouter(prefix="/api/memories", tags=["memories"])

@router.get("/search", response_model=MemorySearchResponse)
def search_memories(
    q: str = Query(..., description="Search query"),
    limit: int = Query(10, ge=1, le=100, description="Number of results"),
    min_score: float = Query(0.5, ge=0.0, le=1.0, description="Minimum cosine similarity"),
    db: Session = Depends(get_db),
):
    """Search memories by vector similarity."""
    # Embed the query
    query_embedding = embed_text(q)

    # Query pgvector for cosine similarity
    results = db.execute(
        """
        SELECT m.id, m.fact, m.created_at, vector_similarity(m.vector, :query_embedding) AS score
        FROM memoryweb.memories m
        WHERE m.tombstoned_at IS NULL
        ORDER BY score DESC
        LIMIT :limit
        """,
        {"query_embedding": query_embedding, "limit": limit},
    ).fetchall()

    # Filter by minimum score
    results = [result for result in results if result["score"] >= min_score]

    # Return the results
    return MemorySearchResponse(
        query=q,
        results=[
            {
                "memory_id": result["id"],
                "content": result["fact"],
                "score": result["score"],
                "created_at": result["created_at"],
            }
            for result in results
        ],
    )

---FILE: app/schemas.py---
"""Schemas for API requests and responses."""
from pydantic import BaseModel
from typing import List, Optional

class MemorySearchRequest(BaseModel):
    q: str
    limit: Optional[int] = 10
    min_score: Optional[float] = 0.5

class MemorySearchResponse(BaseModel):
    query: str
    results: List[dict]

---FILE: app/services/ingestion.py---
"""Ingestion service."""
from typing import Optional
import numpy as np
from ..deps import get_db
from ..models import Memory

def embed_text(text: str) -> np.ndarray:
    """Embed text using the existing embedding service."""
    # Implement your embedding service here
    # For demonstration purposes, a simple embedding service is used
    return np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])


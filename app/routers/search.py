"""Search API routes."""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import Memory, MemoryProvenance, Segment, Source, Tag, TagAxis
from ..schemas import (
    ConversationOut,
    MemoryListResponse,
    MemoryOut,
    SearchRequest,
    SearchResponse,
    SegmentOut,
)
from ..services.retrieval import search

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
def full_search(body: SearchRequest):
    """Full tiered search (Tier 1 → 2 → 3) with filters and provenance."""
    return search(
        query=body.query,
        filters=body.filters,
        k=body.k,
        include_tombstoned=body.include_tombstoned,
        include_superseded=body.include_superseded,
        min_tier=body.min_tier,
        force_tier=body.force_tier,
    )


@router.get("/by-tag", response_model=SearchResponse)
def search_by_tag(
    axis: str = Query(..., description="Tag axis: domain/intent/sensitivity/importance/project"),
    value: str = Query(...),
    k: int = Query(default=10, le=100),
):
    """Search by a specific tag axis and value."""
    return search(query="", filters={axis: value}, k=k)


@router.get("/by-entity", response_model=SearchResponse)
def search_by_entity(
    name: str = Query(...),
    entity_type: Optional[str] = Query(default=None),
    k: int = Query(default=10, le=100),
):
    """Search memories mentioning a specific entity."""
    return search(query=name, filters={"entity": name}, k=k)


@router.get("/by-date", response_model=SearchResponse)
def search_by_date(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    query: str = Query(default=""),
    k: int = Query(default=10, le=100),
):
    """Search within a date range."""
    filters = {}
    if date_from:
        filters["date_from"] = str(date_from)
    if date_to:
        filters["date_to"] = str(date_to)
    return search(query=query, filters=filters, k=k)


@router.get("/by-conversation/{conversation_id}", response_model=SearchResponse)
def search_by_conversation(
    conversation_id: int,
    query: str = Query(default=""),
):
    """Search within a specific conversation."""
    return search(query=query, filters={"conversation_id": conversation_id})

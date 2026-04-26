"""Pydantic request/response schemas for MemoryWeb API."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


# ---------------------------------------------------------------------------
# Source schemas
# ---------------------------------------------------------------------------
class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_type: str
    source_path: str
    source_hash: str
    file_size_bytes: Optional[int]
    message_count: Optional[int]
    ingested_at: Optional[datetime]
    # Migration 009: trust tier + invalidation
    trust_tier: int = 4
    invalidated_at: Optional[datetime] = None
    invalidation_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Ingest schemas
# ---------------------------------------------------------------------------
class IngestSessionRequest(BaseModel):
    path: str = Field(..., description="Absolute path to .jsonl session file")
    force: bool = Field(default=False, description="Re-ingest even if hash unchanged")


class IngestAllSessionsRequest(BaseModel):
    directory: Optional[str] = Field(default=None, description="Override sessions directory")
    force: bool = False


class IngestSharedChatRequest(BaseModel):
    directory: Optional[str] = Field(default=None)
    limit: Optional[int] = Field(default=None, description="Max files to ingest")
    force: bool = False


class IngestSqliteMemoryRequest(BaseModel):
    path: Optional[str] = Field(default=None, description="Override memory.db path")


class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str


class IngestStatusResponse(BaseModel):
    task_id: str
    status: str
    stage: Optional[str] = None
    records_processed: Optional[int] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Search schemas
# ---------------------------------------------------------------------------
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    filters: Optional[Dict[str, Any]] = Field(default=None)
    k: int = Field(default=10, ge=1, le=100)
    include_tombstoned: bool = False
    min_tier: int = Field(default=1, ge=1, le=3, description="Minimum retrieval tier to use")
    force_tier: Optional[int] = Field(default=None, ge=1, le=3, description="Run exactly this tier only (overrides min_tier)")
    include_superseded: bool = Field(default=False, description="Include memories invalidated by contradiction detection")


class ProvenanceChain(BaseModel):
    memory_id: Optional[int] = None
    segment_id: Optional[int] = None
    message_id: Optional[int] = None
    source_id: Optional[int] = None
    source_path: Optional[str] = None
    char_offset_start: Optional[int] = None
    char_offset_end: Optional[int] = None
    derivation_type: Optional[str] = None


class SearchResult(BaseModel):
    result_type: str   # memory|segment|message
    id: int
    content: str
    score: float
    tier: int          # 1|2|3 which tier retrieved this
    tags: List[Dict[str, Any]] = []
    provenance: List[ProvenanceChain] = []
    tombstoned: bool = False


class SearchResponse(BaseModel):
    query: str
    total: int
    results: List[SearchResult]
    tiers_used: List[int]
    latency_ms: float


# ---------------------------------------------------------------------------
# Memory schemas
# ---------------------------------------------------------------------------
class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fact: str
    category: Optional[str]
    confidence: Optional[float]
    importance: Optional[int]
    access_count: int = 0
    created_at: Optional[datetime] = None
    tombstoned_at: Optional[datetime]
    # Temporal validity (Phase 5)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    superseded_by: Optional[int] = None
    is_current: bool = True  # True when valid_until IS NULL — populated by ORM @property
    # Migration 007: trust + provenance
    source_id: Optional[int] = None
    derivation_tier: int = 4
    ingested_at: Optional[datetime] = None
    corroboration_count: int = 1
    # Migration 008: dedup group
    canonical_group_id: Optional[int] = None
    # Migration 011: keyword expansion
    search_keywords: List[str] = Field(default_factory=list)
    # Migration 012: integrity upgrade
    corrected_at: Optional[datetime] = None
    required_roots: int = 1
    source_class: str = "unknown"
    belief_state: str = "active"

    @field_validator("search_keywords", mode="before")
    @classmethod
    def coerce_none_keywords(cls, v):
        # C-4 fix: the DB column has no NOT NULL constraint, so any row inserted
        # without specifying search_keywords has NULL. Coerce to empty list so
        # model_validate never raises ValidationError on legacy/raw-SQL rows.
        return v if v is not None else []


class MemoryWithProvenance(MemoryOut):
    provenance: List[ProvenanceChain] = []


class MemoryListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[MemoryOut]


# ---------------------------------------------------------------------------
# Conversation / Segment / Message schemas
# ---------------------------------------------------------------------------
class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    external_id: Optional[str]
    title: Optional[str]
    participant: Optional[str]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    message_count: int


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    start_ordinal: int
    end_ordinal: int
    summary: Optional[str]
    message_count: int
    tombstoned_at: Optional[datetime]
    tags: List[Dict[str, Any]] = []


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    ordinal: int
    role: str
    content: Optional[str]
    external_uuid: Optional[str]
    char_offset_start: Optional[int]
    char_offset_end: Optional[int]
    sent_at: Optional[datetime]
    tombstoned_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Retention schemas
# ---------------------------------------------------------------------------
class RetentionRequest(BaseModel):
    reason: Optional[str] = None


class PurgeRequest(BaseModel):
    older_than_days: int = Field(default=30, ge=1)
    dry_run: bool = True


class RetentionResult(BaseModel):
    action: str
    target_type: str
    affected_count: int
    affected_ids: List[int] = []
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Status schemas
# ---------------------------------------------------------------------------
class ServiceStatus(BaseModel):
    name: str
    healthy: bool
    detail: Optional[str] = None


class StatsOut(BaseModel):
    sources: int
    conversations: int
    messages: int
    segments: int
    memories: int
    embeddings: int
    tombstoned_memories: int


class PipelineHealthOut(BaseModel):
    done: int
    pending: int
    running: int
    failed: int
    total: int
    # Ingestion completeness audit (Phase 2c)
    failed_segments: int = 0          # segments with 0 memories synthesized
    orphaned_conversations: int = 0   # conversations with messages but no segments
    embedding_coverage: float = 0.0   # % of memories that have embeddings
    stalled_queue: int = 0            # embedding_queue items stuck in 'running' >1h
    permanently_failed: int = 0       # pipeline runs given up after max retries


class StatusResponse(BaseModel):
    services: List[ServiceStatus]
    stats: StatsOut
    pipeline_health: Optional[PipelineHealthOut] = None
    version: str = "0.1.0"


# ---------------------------------------------------------------------------
# Event log schemas (Migration 010)
# ---------------------------------------------------------------------------
class EventLogOut(BaseModel):
    id: int
    event_type: str
    target_type: str
    target_id: int
    payload: Dict[str, Any]
    hash: str
    prev_hash: Optional[str]
    created_at: Optional[str]  # ISO string from service layer


class MemoryHistoryOut(BaseModel):
    memory_id: int
    events: List[EventLogOut]
    total: int


class EventLogVerifyOut(BaseModel):
    valid: bool
    chain_length: int
    first_broken_at: Optional[int]
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Source invalidation schemas (Migration 009)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Answer certificate schemas (Migration 012)
# ---------------------------------------------------------------------------
class AnswerCertificateOut(BaseModel):
    id: int
    query_text: str
    answer_text: Optional[str] = None
    confidence: Optional[float] = None
    stale_reason: Optional[str] = None
    stale_at: Optional[datetime] = None
    cleared_at: Optional[datetime] = None
    created_at: datetime
    memory_ids: List[int] = Field(default_factory=list)
    source_ids: List[int] = Field(default_factory=list)


class CertificateListResponse(BaseModel):
    total: int
    items: List[AnswerCertificateOut]


# ---------------------------------------------------------------------------
# Source invalidation schemas (Migration 009)
# ---------------------------------------------------------------------------
class SourceInvalidateRequest(BaseModel):
    reason: str = ""


class SourceInvalidateResult(BaseModel):
    source_id: int
    action: str  # "invalidated" | "restored"
    affected_memories: int

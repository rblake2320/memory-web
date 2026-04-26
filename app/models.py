"""
SQLAlchemy ORM models for the memoryweb schema.
All tables live in the 'memoryweb' PostgreSQL schema.
"""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Integer, SmallInteger, String, Text, UniqueConstraint,
    Index, Float, ARRAY, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, ARRAY as PG_ARRAY, ARRAY as PGARR
from sqlalchemy.orm import relationship
import os as _os

def _check_pgvector_in_db() -> bool:
    """
    Check whether the PostgreSQL 'vector' extension is actually installed.
    Returns False if not reachable or extension absent.
    Run at import time only when needed.

    Reads MW_DATABASE_URL from the .env file first (pydantic-settings does NOT
    populate os.environ, so os.environ.get would always miss it and fall back to
    the wrong hardcoded URL).
    """
    try:
        import psycopg2
        from urllib.parse import unquote
        # Try .env file first (pydantic-settings doesn't populate os.environ)
        db_url = None
        try:
            from dotenv import dotenv_values
            _env_path = _os.path.join(_os.path.dirname(__file__), '..', '.env')
            _env = dotenv_values(_env_path)
            db_url = _env.get('MW_DATABASE_URL')
        except Exception:
            pass
        # Fall back to os.environ if dotenv didn't find it
        if not db_url:
            db_url = _os.environ.get('MW_DATABASE_URL', '')
        if not db_url:
            return False
        db_url_decoded = unquote(db_url)
        conn = psycopg2.connect(db_url_decoded)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        result = cur.fetchone() is not None
        conn.close()
        return result
    except Exception:
        return False


_HAS_PGVECTOR = _check_pgvector_in_db()

if _HAS_PGVECTOR:
    from pgvector.sqlalchemy import Vector
    _VECTOR_TYPE = Vector(384)
else:
    _VECTOR_TYPE = Text()   # fallback: store as JSON string

from .database import Base, SCHEMA


def ts():
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("source_hash", name="uq_sources_hash"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    source_type = Column(String(50), nullable=False)   # claude_session|shared_chat|sqlite_memory
    source_path = Column(Text, nullable=False)
    source_hash = Column(String(64), nullable=False)   # SHA-256 hex
    file_size_bytes = Column(BigInteger, nullable=True)
    message_count = Column(Integer, nullable=True)
    ingested_at = Column(DateTime, default=ts)
    last_checked_at = Column(DateTime, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=True)
    # Migration 009: source trust tier + invalidation
    trust_tier = Column(SmallInteger, nullable=False, default=4, server_default="4")
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    invalidation_reason = Column(Text, nullable=True)

    conversations = relationship("Conversation", back_populates="source")
    pipeline_runs = relationship("PipelineRun", back_populates="source")


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------
class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = {"schema": SCHEMA}

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    source_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.sources.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id = Column(String(255), nullable=True, index=True)  # e.g. JSONL session UUID
    title = Column(Text, nullable=True)
    participant = Column(String(100), nullable=True)  # user handle or model name
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    message_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=ts)

    source = relationship("Source", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", order_by="Message.ordinal")
    segments = relationship("Segment", back_populates="conversation")


# ---------------------------------------------------------------------------
# messages  (immutable raw - never mutated)
# ---------------------------------------------------------------------------
class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index(f"ix_{SCHEMA}_messages_conv_ordinal", "conversation_id", "ordinal"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    conversation_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    ordinal = Column(Integer, nullable=False)          # position within conversation
    role = Column(String(50), nullable=False)          # user|assistant|system
    content = Column(Text, nullable=True)
    raw_json = Column(JSONB, nullable=True)            # original parsed JSON blob
    external_uuid = Column(String(255), nullable=True, index=True)
    char_offset_start = Column(BigInteger, nullable=True)  # byte offset in source file
    char_offset_end = Column(BigInteger, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    token_count = Column(Integer, nullable=True)
    tombstoned_at = Column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="messages")
    entity_mentions = relationship("EntityMention", back_populates="message")
    provenance_links = relationship("MemoryProvenance", back_populates="message")


# ---------------------------------------------------------------------------
# segments  (topical slices of conversations)
# ---------------------------------------------------------------------------
class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("conversation_id", "start_ordinal", "end_ordinal", name="uq_segments_conv_ordinal"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    conversation_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    start_message_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.messages.id"), nullable=False)
    end_message_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.messages.id"), nullable=False)
    start_ordinal = Column(Integer, nullable=False)
    end_ordinal = Column(Integer, nullable=False)
    summary = Column(Text, nullable=True)
    model_used = Column(String(100), nullable=True)   # Ollama model that summarised
    message_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=ts)
    tombstoned_at = Column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="segments")
    tags = relationship("Tag", back_populates="segment")
    entity_mentions = relationship("EntityMention", back_populates="segment")
    provenance_links = relationship("MemoryProvenance", back_populates="segment")
    embeddings = relationship("Embedding", primaryjoin=f"and_(Embedding.target_type=='segment', foreign(Embedding.target_id)==Segment.id)", overlaps="embeddings")


# ---------------------------------------------------------------------------
# tag_axes  (axis definitions)
# ---------------------------------------------------------------------------
class TagAxis(Base):
    __tablename__ = "tag_axes"
    __table_args__ = (
        UniqueConstraint("axis_name", name="uq_tag_axes_name"),
        {"schema": SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    axis_name = Column(String(50), nullable=False)   # domain|intent|sensitivity|importance|project
    description = Column(Text, nullable=True)

    tags = relationship("Tag", back_populates="axis")


# ---------------------------------------------------------------------------
# tags  (multi-axis tags on segments)
# ---------------------------------------------------------------------------
class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("segment_id", "axis_id", "value", name="uq_tags_segment_axis_value"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    segment_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.segments.id", ondelete="CASCADE"), nullable=False, index=True)
    axis_id = Column(Integer, ForeignKey(f"{SCHEMA}.tag_axes.id"), nullable=False)
    value = Column(String(200), nullable=False)
    confidence = Column(Float, nullable=True)  # 0.0–1.0
    created_at = Column(DateTime, default=ts)

    segment = relationship("Segment", back_populates="tags")
    axis = relationship("TagAxis", back_populates="tags")


# ---------------------------------------------------------------------------
# entities  (canonical named entities with trigram GIN index)
# ---------------------------------------------------------------------------
class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("canonical_name", "entity_type", name="uq_entities_canonical"),
        Index(f"ix_{SCHEMA}_entities_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(500), nullable=False)
    entity_type = Column(String(50), nullable=False)  # ip|path|model|service|person|project|...
    canonical_name = Column(String(500), nullable=False)
    created_at = Column(DateTime, default=ts)

    mentions = relationship("EntityMention", back_populates="entity")


# ---------------------------------------------------------------------------
# entity_mentions  (entity-to-segment/message links)
# ---------------------------------------------------------------------------
class EntityMention(Base):
    __tablename__ = "entity_mentions"
    __table_args__ = (
        Index(f"ix_{SCHEMA}_em_segment", "segment_id"),
        Index(f"ix_{SCHEMA}_em_message", "message_id"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    entity_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.entities.id", ondelete="CASCADE"), nullable=False)
    segment_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.segments.id", ondelete="CASCADE"), nullable=True)
    message_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.messages.id", ondelete="CASCADE"), nullable=True)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)
    context_snippet = Column(Text, nullable=True)

    entity = relationship("Entity", back_populates="mentions")
    segment = relationship("Segment", back_populates="entity_mentions")
    message = relationship("Message", back_populates="entity_mentions")


# ---------------------------------------------------------------------------
# memories  (atomic facts - the core retrieval target)
# ---------------------------------------------------------------------------
class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("fact_hash", name="uq_memories_fact_hash"),
        Index(f"ix_{SCHEMA}_memories_trgm", "fact", postgresql_using="gin", postgresql_ops={"fact": "gin_trgm_ops"}),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    fact = Column(Text, nullable=False)
    fact_hash = Column(String(64), nullable=False)     # SHA-256 of normalised fact
    category = Column(String(100), nullable=True)      # e.g. infrastructure|preference|decision
    confidence = Column(Float, nullable=True)
    importance = Column(SmallInteger, nullable=True)   # 1-5
    access_count = Column(Integer, default=0)
    last_accessed_at = Column(DateTime, nullable=True)
    # Utility scoring (migration 003)
    retrieval_count = Column(Integer, default=0, nullable=False, server_default="0")
    helpful_count = Column(Integer, default=0, nullable=False, server_default="0")
    utility_score = Column(Float, default=0.5, nullable=False, server_default="0.5")
    created_at = Column(DateTime, default=ts)
    tombstoned_at = Column(DateTime, nullable=True)
    # Temporal validity (migration 006) — bi-temporal contradiction handling
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)   # set when superseded
    superseded_by = Column(BigInteger, ForeignKey(f"memoryweb.memories.id", ondelete="SET NULL"), nullable=True)
    # Migration 007: provenance trust columns
    source_id = Column(BigInteger, ForeignKey(f"memoryweb.sources.id", ondelete="SET NULL"), nullable=True)
    derivation_tier = Column(SmallInteger, nullable=False, default=4, server_default="4")
    ingested_at = Column(DateTime(timezone=True), nullable=False, server_default="now()")
    corroboration_count = Column(Integer, nullable=False, default=1, server_default="1")
    # Migration 008: canonical dedup group
    canonical_group_id = Column(BigInteger, nullable=True)
    # Migration 009: pre-invalidation confidence snapshot for /restore
    pre_invalidation_confidence = Column(Float, nullable=True)
    # Migration 011: write-time search keyword expansion
    search_keywords = Column(PG_ARRAY(Text), nullable=False, default=list, server_default="{}")
    # Migration 012: integrity upgrade
    corrected_at = Column(DateTime(timezone=True), nullable=True)
    required_roots = Column(SmallInteger, nullable=False, default=1, server_default="1")
    source_class = Column(String(30), nullable=False, default="unknown", server_default="unknown")
    belief_state = Column(String(20), nullable=False, default="active", server_default="active")
    base_trust = Column(Float, nullable=True)
    # Migration 013a: bitemporal transaction time (when system recorded, vs valid_from = when true in world)
    transaction_time = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    provenance = relationship("MemoryProvenance", back_populates="memory")
    links_a = relationship("MemoryLink", foreign_keys="MemoryLink.memory_id_a", back_populates="memory_a")
    links_b = relationship("MemoryLink", foreign_keys="MemoryLink.memory_id_b", back_populates="memory_b")
    embeddings = relationship("Embedding", primaryjoin=f"and_(Embedding.target_type=='memory', foreign(Embedding.target_id)==Memory.id)", overlaps="embeddings")

    @property
    def is_current(self) -> bool:
        """W-1 fix: reflect actual valid_until state instead of always returning True."""
        return self.valid_until is None


# ---------------------------------------------------------------------------
# memory_provenance  (where-from links)
# ---------------------------------------------------------------------------
class MemoryProvenance(Base):
    __tablename__ = "memory_provenance"
    __table_args__ = (
        Index(f"ix_{SCHEMA}_mp_memory", "memory_id"),
        Index(f"ix_{SCHEMA}_mp_source", "source_id"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    memory_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.memories.id", ondelete="CASCADE"), nullable=False)
    segment_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.segments.id", ondelete="SET NULL"), nullable=True)
    message_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.messages.id", ondelete="SET NULL"), nullable=True)
    source_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.sources.id", ondelete="SET NULL"), nullable=True)
    derivation_type = Column(String(50), nullable=False, default="extracted")  # extracted|synthesised|imported
    created_at = Column(DateTime, default=ts)

    memory = relationship("Memory", back_populates="provenance")
    segment = relationship("Segment", back_populates="provenance_links")
    message = relationship("Message", back_populates="provenance_links")
    source = relationship("Source")


# ---------------------------------------------------------------------------
# memory_links  (memory-to-memory graph edges)
# ---------------------------------------------------------------------------
class MemoryLink(Base):
    __tablename__ = "memory_links"
    __table_args__ = (
        UniqueConstraint("memory_id_a", "memory_id_b", "link_type", name="uq_memory_links"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    memory_id_a = Column(BigInteger, ForeignKey(f"{SCHEMA}.memories.id", ondelete="CASCADE"), nullable=False)
    memory_id_b = Column(BigInteger, ForeignKey(f"{SCHEMA}.memories.id", ondelete="CASCADE"), nullable=False)
    link_type = Column(String(50), nullable=False)  # supports|contradicts|supersedes|related
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, default=ts)

    memory_a = relationship("Memory", foreign_keys=[memory_id_a], back_populates="links_a")
    memory_b = relationship("Memory", foreign_keys=[memory_id_b], back_populates="links_b")


# ---------------------------------------------------------------------------
# embeddings  (pgvector 384-dim, IVFFlat cosine)
# ---------------------------------------------------------------------------
class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("target_type", "target_id", name="uq_embeddings_target"),
        Index(f"ix_{SCHEMA}_embeddings_target", "target_type", "target_id"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    target_type = Column(String(20), nullable=False)  # segment|memory
    target_id = Column(BigInteger, nullable=False)
    vector = Column(_VECTOR_TYPE)
    model = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=ts)


# ---------------------------------------------------------------------------
# retention_log  (audit trail for all deletions)
# ---------------------------------------------------------------------------
class RetentionLog(Base):
    __tablename__ = "retention_log"
    __table_args__ = {"schema": SCHEMA}

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    action = Column(String(50), nullable=False)     # tombstone|restore|purge
    target_type = Column(String(50), nullable=False)
    target_ids = Column(JSONB, nullable=True)       # array of affected IDs
    reason = Column(Text, nullable=True)
    triggered_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=ts)
    reversed_at = Column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# pipeline_runs  (pipeline progress tracking)
# ---------------------------------------------------------------------------
class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index(f"ix_{SCHEMA}_pipeline_runs_source", "source_id"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    source_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.sources.id", ondelete="CASCADE"), nullable=True)
    stage = Column(String(50), nullable=False)       # ingest|segment|tag|extract|synthesize|embed
    status = Column(String(20), nullable=False, default="pending")  # pending|running|done|failed|permanently_failed
    celery_task_id = Column(String(255), nullable=True)
    records_processed = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=ts)
    # Phase 1c: attempt counter prevents infinite retry storms
    attempts = Column(Integer, default=0, nullable=False, server_default="0")

    source = relationship("Source", back_populates="pipeline_runs")


# ---------------------------------------------------------------------------
# embedding_queue  (decoupled queue for sentence-transformer embedding jobs)
# ---------------------------------------------------------------------------
class EmbeddingQueue(Base):
    __tablename__ = "embedding_queue"
    __table_args__ = (
        Index(f"ix_{SCHEMA}_eq_status", "status"),
        Index(f"ix_{SCHEMA}_eq_target", "target_type", "target_id"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    target_type = Column(String(20), nullable=False, default="memory")  # memory|segment
    target_id = Column(BigInteger, nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # pending|running|done|failed
    queued_at = Column(DateTime(timezone=True), server_default="now()")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)
    attempts = Column(Integer, default=0, nullable=False, server_default="0")


# ---------------------------------------------------------------------------
# event_log  (migration 010: append-only ledger with SHA-256 hash chain)
# ---------------------------------------------------------------------------
class EventLog(Base):
    __tablename__ = "event_log"
    __table_args__ = (
        Index(f"ix_{SCHEMA}_event_log_target", "target_type", "target_id", "created_at"),
        Index(f"ix_{SCHEMA}_event_log_hash", "hash"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    event_type = Column(Text, nullable=False)   # memory_created|memory_superseded|source_invalidated|confidence_changed
    target_type = Column(Text, nullable=False)  # memory|source
    target_id = Column(BigInteger, nullable=False)
    payload = Column(JSONB, nullable=False)     # full before/after state
    hash = Column(Text, nullable=False)         # SHA-256 of (prev_hash + event_type + target_id + payload)
    prev_hash = Column(Text, nullable=True)     # hash of previous event (chain link)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default="now()")
    # Migration 012: idempotent deduplication key
    dedupe_key = Column(Text, nullable=True)


# ---------------------------------------------------------------------------
# answer_certificates  (Migration 012: what was returned to each query)
# ---------------------------------------------------------------------------
class AnswerCertificate(Base):
    __tablename__ = "answer_certificates"
    __table_args__ = (
        Index(f"ix_mw_answer_certs_created", "created_at"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    query_text = Column(Text, nullable=False)
    answer_text = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    stale_reason = Column(Text, nullable=True)
    stale_at = Column(DateTime(timezone=True), nullable=True)
    cleared_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default="now()")

    memory_links = relationship("AnswerCertificateMemory", back_populates="certificate",
                                cascade="all, delete-orphan")
    source_links = relationship("AnswerCertificateSource", back_populates="certificate",
                                cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# answer_certificate_memories  (memory lineage junction)
# ---------------------------------------------------------------------------
class AnswerCertificateMemory(Base):
    __tablename__ = "answer_certificate_memories"
    __table_args__ = (
        UniqueConstraint("certificate_id", "memory_id", name="uq_acm_cert_mem"),
        Index("ix_mw_acm_cert", "certificate_id"),
        Index("ix_mw_acm_mem", "memory_id"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    certificate_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.answer_certificates.id",
                            ondelete="CASCADE"), nullable=False)
    memory_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.memories.id",
                       ondelete="CASCADE"), nullable=False)

    certificate = relationship("AnswerCertificate", back_populates="memory_links")


# ---------------------------------------------------------------------------
# answer_certificate_sources  (source lineage junction)
# ---------------------------------------------------------------------------
class AnswerCertificateSource(Base):
    __tablename__ = "answer_certificate_sources"
    __table_args__ = (
        UniqueConstraint("certificate_id", "source_id", name="uq_acs_cert_src"),
        Index("ix_mw_acs_cert", "certificate_id"),
        Index("ix_mw_acs_src", "source_id"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Migration 013a
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True, server_default="00000000-0000-0000-0000-000000000001")
    certificate_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.answer_certificates.id",
                            ondelete="CASCADE"), nullable=False)
    source_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.sources.id",
                       ondelete="CASCADE"), nullable=False)

    certificate = relationship("AnswerCertificate", back_populates="source_links")


# ---------------------------------------------------------------------------
# tenants  (Migration 013a: multi-tenant isolation)
# ---------------------------------------------------------------------------
class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = {"schema": SCHEMA}

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False)
    email = Column(String(255), nullable=True)
    plan = Column(String(30), nullable=False, default="community", server_default="community")
    disabled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    api_keys = relationship("TenantApiKey", back_populates="tenant", cascade="all, delete-orphan")
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# tenant_api_keys  (hashed API keys with scopes and revocation)
# ---------------------------------------------------------------------------
class TenantApiKey(Base):
    __tablename__ = "tenant_api_keys"
    __table_args__ = (
        Index(f"ix_{SCHEMA}_tak_tenant", "tenant_id"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False)
    key_prefix = Column(String(12), nullable=False)    # first 12 chars, displayable
    key_hash = Column(String(128), nullable=False)     # bcrypt hash of full key
    name = Column(String(255), nullable=True)          # user label e.g. "Production API"
    scopes = Column(PG_ARRAY(Text), nullable=False, default=list, server_default="{}")
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    tenant = relationship("Tenant", back_populates="api_keys")


# ---------------------------------------------------------------------------
# users  (Migration 013a: per-tenant user accounts)
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),  # global — one account per email
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(30), nullable=False, default="user", server_default="user")  # user|admin
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    tenant = relationship("Tenant", back_populates="users")

"""
SQLAlchemy ORM models for the memoryweb schema.
All tables live in the 'memoryweb' PostgreSQL schema.
"""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Integer, SmallInteger, String, Text, UniqueConstraint,
    Index, Float, ARRAY,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, ARRAY as PG_ARRAY
from sqlalchemy.orm import relationship
import os as _os

def _check_pgvector_in_db() -> bool:
    """
    Check whether the PostgreSQL 'vector' extension is actually installed.
    Returns False if not reachable or extension absent.
    Run at import time only when needed.
    """
    try:
        import psycopg2
        db_url = _os.environ.get(
            "MW_DATABASE_URL",
            "postgresql://memoryweb:memoryweb@localhost:5432/memoryweb",
        )
        # urllib.parse.unquote for %3F → ?
        from urllib.parse import unquote
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
    source_type = Column(String(50), nullable=False)   # claude_session|shared_chat|sqlite_memory
    source_path = Column(Text, nullable=False)
    source_hash = Column(String(64), nullable=False)   # SHA-256 hex
    file_size_bytes = Column(BigInteger, nullable=True)
    message_count = Column(Integer, nullable=True)
    ingested_at = Column(DateTime, default=ts)
    last_checked_at = Column(DateTime, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=True)

    conversations = relationship("Conversation", back_populates="source")
    pipeline_runs = relationship("PipelineRun", back_populates="source")


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------
class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = {"schema": SCHEMA}

    id = Column(BigInteger, primary_key=True, autoincrement=True)
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

    provenance = relationship("MemoryProvenance", back_populates="memory")
    links_a = relationship("MemoryLink", foreign_keys="MemoryLink.memory_id_a", back_populates="memory_a")
    links_b = relationship("MemoryLink", foreign_keys="MemoryLink.memory_id_b", back_populates="memory_b")
    embeddings = relationship("Embedding", primaryjoin=f"and_(Embedding.target_type=='memory', foreign(Embedding.target_id)==Memory.id)", overlaps="embeddings")


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
    source_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.sources.id", ondelete="CASCADE"), nullable=True)
    stage = Column(String(50), nullable=False)       # ingest|segment|tag|extract|synthesize|embed
    status = Column(String(20), nullable=False, default="pending")  # pending|running|done|failed
    celery_task_id = Column(String(255), nullable=True)
    records_processed = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=ts)

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
    target_type = Column(String(20), nullable=False, default="memory")  # memory|segment
    target_id = Column(BigInteger, nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # pending|running|done|failed
    queued_at = Column(DateTime(timezone=True), server_default="now()")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)
    attempts = Column(Integer, default=0, nullable=False, server_default="0")

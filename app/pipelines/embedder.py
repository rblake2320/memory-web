"""
Embedder: sentence-transformers → pgvector IVFFlat index.
Embeds both segments (summary + content) and memories (fact text).
"""

import logging
from typing import List, Optional

import numpy as np
from sqlalchemy import text

from ..config import settings
from ..database import db_session, engine
from ..models import Embedding, Memory, Message, Segment

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(settings.MW_EMBED_MODEL)
    return _model


def _upsert_embedding(
    db,
    target_type: str,
    target_id: int,
    vector: List[float],
) -> None:
    """Delete old embedding for this target and insert new one."""
    db.query(Embedding).filter(
        Embedding.target_type == target_type,
        Embedding.target_id == target_id,
    ).delete()

    emb = Embedding(
        target_type=target_type,
        target_id=target_id,
        vector=vector,
        model=settings.MW_EMBED_MODEL,
    )
    db.add(emb)


def embed_segments(conversation_id: int) -> int:
    """Embed all segments for a conversation. Returns count embedded."""
    with db_session() as db:
        segments = (
            db.query(Segment)
            .filter(
                Segment.conversation_id == conversation_id,
                Segment.tombstoned_at.is_(None),
            )
            .all()
        )

        if not segments:
            return 0

        model = _get_model()
        texts = []
        seg_ids = []

        for seg in segments:
            # Build text to embed: summary + first few messages
            content_parts = []
            if seg.summary:
                content_parts.append(seg.summary)

            messages = (
                db.query(Message)
                .filter(
                    Message.conversation_id == seg.conversation_id,
                    Message.ordinal >= seg.start_ordinal,
                    Message.ordinal <= seg.end_ordinal,
                    Message.tombstoned_at.is_(None),
                )
                .order_by(Message.ordinal)
                .limit(10)
                .all()
            )
            content_parts.extend(m.content or "" for m in messages)
            text = " ".join(content_parts)[:512]  # cap for model

            texts.append(text)
            seg_ids.append(seg.id)

        if not texts:
            return 0

        # Batch encode
        batch_size = settings.MW_EMBED_BATCH_SIZE
        all_vecs = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            vecs = model.encode(batch, normalize_embeddings=True).astype(np.float32)
            all_vecs.extend(vecs.tolist())

        for seg_id, vec in zip(seg_ids, all_vecs):
            _upsert_embedding(db, "segment", seg_id, vec)

        db.flush()
        return len(seg_ids)


def embed_memories(source_id: Optional[int] = None, batch_size: int = 64) -> int:
    """
    Embed Memory records. If source_id given, only embed memories from that source.
    Returns count embedded.
    """
    with db_session() as db:
        query = db.query(Memory).filter(Memory.tombstoned_at.is_(None))
        if source_id:
            from ..models import MemoryProvenance
            memory_ids = [
                r[0] for r in db.query(MemoryProvenance.memory_id)
                .filter(MemoryProvenance.source_id == source_id)
                .distinct()
                .all()
            ]
            query = query.filter(Memory.id.in_(memory_ids))

        memories = query.all()
        if not memories:
            return 0

        model = _get_model()
        texts = [m.fact[:512] for m in memories]
        mem_ids = [m.id for m in memories]

        all_vecs = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            vecs = model.encode(batch, normalize_embeddings=True).astype(np.float32)
            all_vecs.extend(vecs.tolist())

        for mem_id, vec in zip(mem_ids, all_vecs):
            _upsert_embedding(db, "memory", mem_id, vec)

        db.flush()
        return len(mem_ids)


def _build_ivfflat_index_DEPRECATED(lists: int = 100) -> None:
    """DEPRECATED: System uses HNSW. Do NOT call — kept for reference only.

    The production index is ix_embeddings_hnsw (HNSW, ef_search=100).
    IVFFlat would add a redundant index, wasting write amplification.
    """
    raise NotImplementedError(
        "IVFFlat index is deprecated. System uses HNSW. "
        "See migration 004 and ALTER DATABASE ... SET memoryweb.hnsw.ef_search."
    )

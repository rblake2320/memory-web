"""
Memory synthesizer: extract atomic facts from segments with provenance pointers.

Each atomic fact becomes a Memory record linked back to its source segment/messages.
Dedup by fact_hash (SHA-256 of normalised fact text).

Phase 1 fixes:
  - Windowed synthesis (3000-char windows, 200-char overlap) — no more silent 94% truncation
  - 25-fact cap instead of 10, with warning when hit
  - Ollama pre-flight before any LLM call — fails loud instead of silent
  - OllamaUnavailableError / SynthesisFailedError propagate to pipeline_tasks for recording

Phase 5 addition:
  - Contradiction detection on write: cosine similarity > 0.85 + LLM confirmation
  - Sets valid_until + superseded_by on contradicted memories
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..database import db_session
from ..models import EmbeddingQueue, Memory, MemoryLink, MemoryProvenance, Message, Segment
from ..services.ollama_client import generate_json, is_available as _ollama_ok

logger = logging.getLogger(__name__)

MAX_FACTS_PER_SEGMENT = 25
WINDOW_SIZE = 3000
WINDOW_OVERLAP = 200


# ---------------------------------------------------------------------------
# Custom exceptions — pipeline_tasks catches these to record precise failures
# ---------------------------------------------------------------------------

class OllamaUnavailableError(RuntimeError):
    """Raised when Ollama pre-flight check fails."""


class SynthesisFailedError(RuntimeError):
    """Raised when JSON synthesis call raises an unexpected error."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_fact(fact: str) -> str:
    """Lowercase, strip, collapse whitespace for dedup."""
    return " ".join(fact.lower().split())


def _fact_hash(fact: str) -> str:
    return hashlib.sha256(_normalise_fact(fact).encode()).hexdigest()


def _split_into_windows(content: str) -> List[str]:
    """
    Split content into overlapping 3000-char windows with 200-char overlap.
    A single call to the LLM sees at most 3000 chars, but the full content is
    processed across multiple windows — no data silently discarded.
    """
    if len(content) <= WINDOW_SIZE:
        return [content]
    windows = []
    start = 0
    while start < len(content):
        end = start + WINDOW_SIZE
        windows.append(content[start:end])
        if end >= len(content):
            break
        start = end - WINDOW_OVERLAP
    return windows


def _build_synthesis_prompt(content: str, summary: Optional[str] = None) -> str:
    summary_hint = f"\nConversation summary: {summary}" if summary else ""
    return f"""
Extract atomic facts from this conversation segment.
Each fact should be a single, standalone statement that can be recalled independently.
Focus on: decisions made, configuration values, technical choices, learned information,
user preferences, system states, problems encountered and solutions found.

Return a JSON array. Each element:
{{
  "fact": "A single atomic fact statement",
  "category": "decision|problem|solution|configuration|infrastructure|preference|learning|technical_choice|other",
  "confidence": 0.0-1.0,
  "importance": 1-5
}}

Return 1-25 facts only. No duplicates. Return empty array if no significant facts.
{summary_hint}

Conversation:
{content}

Return ONLY the JSON array.
""".strip()


def _preflight_ollama(segment_id: int) -> None:
    """Raise OllamaUnavailableError if Ollama is not reachable."""
    if not _ollama_ok():
        raise OllamaUnavailableError(
            f"Ollama unavailable — skipping synthesis for segment {segment_id}"
        )


# ---------------------------------------------------------------------------
# Contradiction detection (Phase 5)
# ---------------------------------------------------------------------------

def _check_and_handle_contradictions(
    new_memory: Memory,
    db: Session,
) -> None:
    """
    After creating a new memory, find existing memories with cosine similarity > 0.85
    in the same category and ask Ollama if they contradict.

    Safety: requires BOTH cosine > 0.85 AND LLM confirmation before invalidating.
    Never deletes — only sets valid_until on the old memory.
    """
    from datetime import datetime
    from ..config import settings
    from ..database import engine
    from sqlalchemy import text

    SCHEMA = settings.MW_DB_SCHEMA
    SIMILARITY_THRESHOLD = 0.85

    # Need the new memory to already have an embedding — if not, skip
    # (embeddings are async; contradiction check is best-effort on write)
    with engine.connect() as conn:
        emb_row = conn.execute(
            text(f"""
                SELECT vector FROM {SCHEMA}.embeddings
                WHERE target_type = 'memory' AND target_id = :mid
            """),
            {"mid": new_memory.id},
        ).fetchone()

    if not emb_row:
        # No embedding yet — skip (EmbeddingWorker will add it; contradiction
        # detection on background re-synthesis is a future enhancement)
        return

    qvec = emb_row[0]

    # Find existing memories with similar vectors in the same category
    tomb_filter = "AND m.tombstoned_at IS NULL AND m.valid_until IS NULL"
    cat_filter = ""
    if new_memory.category:
        cat_filter = "AND m.category = :cat"

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT e.target_id, m.fact,
                           1 - (e.vector <=> CAST(:qvec AS vector)) AS sim
                    FROM {SCHEMA}.embeddings e
                    JOIN {SCHEMA}.memories m ON m.id = e.target_id
                    WHERE e.target_type = 'memory'
                      AND e.target_id != :new_id
                      AND 1 - (e.vector <=> CAST(:qvec AS vector)) > :threshold
                      {tomb_filter}
                      {cat_filter}
                    ORDER BY sim DESC
                    LIMIT 5
                """),
                {
                    "qvec": str(qvec),
                    "new_id": new_memory.id,
                    "threshold": SIMILARITY_THRESHOLD,
                    "cat": new_memory.category,
                },
            ).fetchall()
    except Exception as e:
        # pgvector might not be installed or column might not exist yet
        logger.debug("Contradiction check skipped (pgvector query failed): %s", e)
        return

    for old_id, old_fact, sim in rows:
        # Ask Ollama for contradiction confirmation (Agent-Zero pattern: both signals required)
        try:
            result = generate_json(f"""
Do these two facts contradict each other?
Fact A (existing): {old_fact}
Fact B (new): {new_memory.fact}

Return ONLY: {{"contradicts": true}} or {{"contradicts": false}}
""")
            if not isinstance(result, dict) or not result.get("contradicts"):
                # Related but not contradicting — create 'related' link
                try:
                    existing_link = db.query(MemoryLink).filter(
                        MemoryLink.memory_id_a == old_id,
                        MemoryLink.memory_id_b == new_memory.id,
                    ).first()
                    if not existing_link:
                        db.add(MemoryLink(
                            memory_id_a=old_id,
                            memory_id_b=new_memory.id,
                            link_type="related",
                            confidence=float(sim),
                        ))
                except Exception:
                    pass
                continue

            # Confirmed contradiction — invalidate old memory (never delete)
            old_mem = db.query(Memory).get(old_id)
            if old_mem and old_mem.valid_until is None:
                old_mem.valid_until = datetime.utcnow()
                old_mem.superseded_by = new_memory.id
                # Record the supersession link
                db.add(MemoryLink(
                    memory_id_a=old_id,
                    memory_id_b=new_memory.id,
                    link_type="supersedes",
                    confidence=float(sim),
                ))
                logger.info(
                    "Contradiction detected: memory %d superseded by %d (sim=%.3f)",
                    old_id, new_memory.id, sim,
                )
        except Exception as e:
            logger.debug("Contradiction LLM call failed for %d vs %d: %s", old_id, new_memory.id, e)


# ---------------------------------------------------------------------------
# Main synthesis function
# ---------------------------------------------------------------------------

def synthesize_memories_for_segment(segment_id: int) -> int:
    """
    Extract atomic facts from a segment and store as Memory records with provenance.
    Returns number of new memories created.

    Raises:
        OllamaUnavailableError: if Ollama is not reachable before starting
        SynthesisFailedError: if LLM call fails mid-synthesis
    """
    # Pre-flight: fail loud instead of silent
    _preflight_ollama(segment_id)

    with db_session() as db:
        seg = db.query(Segment).get(segment_id)
        if not seg:
            return 0

        # Check if already synthesized
        existing = db.query(MemoryProvenance).filter(
            MemoryProvenance.segment_id == segment_id
        ).count()
        if existing > 0:
            return 0

        messages = (
            db.query(Message)
            .filter(
                Message.conversation_id == seg.conversation_id,
                Message.ordinal >= seg.start_ordinal,
                Message.ordinal <= seg.end_ordinal,
                Message.tombstoned_at.is_(None),
            )
            .order_by(Message.ordinal)
            .all()
        )

        if not messages:
            return 0

        content = "\n".join(
            f"[{m.role.upper()}]: {m.content or ''}"
            for m in messages
        )

        # Get source_id from conversation
        from ..models import Conversation
        conv = db.query(Conversation).get(seg.conversation_id)
        source_id = conv.source_id if conv else None

        # Windowed synthesis: process full content in 3000-char windows
        # Previously content[:3000] silently discarded ~94% of long conversations
        windows = _split_into_windows(content)
        all_facts: List[Dict] = []

        for window_idx, window in enumerate(windows):
            try:
                facts = generate_json(_build_synthesis_prompt(window, seg.summary))
            except Exception as e:
                raise SynthesisFailedError(
                    f"Synthesis failed for segment {segment_id} window {window_idx}: {e}"
                ) from e

            # Handle {"facts": [...]} wrapper or bare list
            if isinstance(facts, dict):
                facts = facts.get("facts", facts.get("memories", []))
            if isinstance(facts, list):
                all_facts.extend(facts)

        if not all_facts:
            return 0

        # Cap at 25 (was 10 — excess facts were silently dropped before)
        if len(all_facts) > MAX_FACTS_PER_SEGMENT:
            logger.warning(
                "Segment %d produced %d facts across %d windows; capping at %d",
                segment_id, len(all_facts), len(windows), MAX_FACTS_PER_SEGMENT,
            )
            all_facts = all_facts[:MAX_FACTS_PER_SEGMENT]

        VALID_CATEGORIES = {
            "decision", "problem", "solution", "configuration",
            "infrastructure", "preference", "learning", "technical_choice", "other",
        }

        created = 0
        new_memories: List[Memory] = []

        for fact_data in all_facts:
            fact_text = fact_data.get("fact", "").strip()
            if not fact_text or len(fact_text) < 10:
                continue

            fhash = _fact_hash(fact_text)

            # Normalize category
            raw_cat = fact_data.get("category", "other").lower().replace(" ", "_").strip()
            if raw_cat.endswith("s") and raw_cat[:-1] in VALID_CATEGORIES:
                raw_cat = raw_cat[:-1]
            category = raw_cat if raw_cat in VALID_CATEGORIES else "other"

            # Dedup check
            existing_mem = db.query(Memory).filter(Memory.fact_hash == fhash).first()
            if existing_mem:
                # Still link provenance
                prov = MemoryProvenance(
                    memory_id=existing_mem.id,
                    segment_id=segment_id,
                    source_id=source_id,
                    derivation_type="extracted",
                )
                db.add(prov)
                continue

            memory = Memory(
                fact=fact_text,
                fact_hash=fhash,
                category=category,
                confidence=float(fact_data.get("confidence", 0.7)),
                importance=int(fact_data.get("importance", 3)),
                access_count=0,
            )
            db.add(memory)
            db.flush()

            # Link mid-point message as primary provenance
            primary_msg = messages[len(messages) // 2]
            prov = MemoryProvenance(
                memory_id=memory.id,
                segment_id=segment_id,
                message_id=primary_msg.id,
                source_id=source_id,
                derivation_type="extracted",
            )
            db.add(prov)
            # Queue for embedding
            db.add(EmbeddingQueue(target_type="memory", target_id=memory.id))
            new_memories.append(memory)
            created += 1

        db.flush()

        # Phase 5: contradiction detection for newly created memories
        # Best-effort: failure here never blocks memory creation
        for mem in new_memories:
            try:
                _check_and_handle_contradictions(mem, db)
            except Exception as e:
                logger.debug("Contradiction check error for memory %d: %s", mem.id, e)

        db.flush()
        return created


def import_sqlite_memory_as_memories(
    journal_entries: List[Any],
    knowledge_items: List[Any],
    source_id: int,
    conversation_id: int,
) -> int:
    """
    Directly import SQLite memory DB entries as Memory records.
    Returns number created.
    """
    created = 0
    with db_session() as db:
        for item in journal_entries + knowledge_items:
            text = getattr(item, "text", None) or getattr(item, "content", "")
            if not text or len(text) < 5:
                continue

            fhash = _fact_hash(text)
            if db.query(Memory).filter(Memory.fact_hash == fhash).first():
                continue

            memory = Memory(
                fact=text[:2000],
                fact_hash=fhash,
                category=getattr(item, "category", None) or getattr(item, "topic", "imported"),
                confidence=0.8,
                importance=3,
            )
            db.add(memory)
            db.flush()

            prov = MemoryProvenance(
                memory_id=memory.id,
                source_id=source_id,
                derivation_type="imported",
            )
            db.add(prov)
            db.add(EmbeddingQueue(target_type="memory", target_id=memory.id))
            created += 1

        db.flush()
    return created

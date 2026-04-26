"""
Memory synthesizer: extract atomic facts from segments with provenance pointers.

Each atomic fact becomes a Memory record linked back to its source segment/messages.
Dedup by fact_hash (SHA-256 of normalised fact text).

Phase 1 fixes:
  - Windowed synthesis (3000-char windows, 200-char overlap) — no more silent 94% truncation
  - 25-fact cap instead of 10, with warning when hit
  - Ollama pre-flight before any LLM call — fails loud instead of silent
  - OllamaUnavailableError / SynthesisFailedError propagate to pipeline_tasks for recording

Migration 007 (fix provenance race condition):
  - Removed _check_and_handle_contradictions from synchronous synthesis
  - Contradiction detection now triggered async via Celery after embedding completes
  - Sets source_id directly on new memories (from conversation.source_id)
  - Sets valid_from = first message's sent_at (world-event time, not system time)
  - Sets ingested_at = datetime.utcnow() (transaction_time)
  - Sets derivation_tier based on first-person detection (3 vs 4)

Migration 009 (trust tiers):
  - First-person facts (I/we/my/our subject) → derivation_tier=3, confidence ceiling 0.75
  - Third-party facts → derivation_tier=4, confidence ceiling 0.65

Migration 010 (event log):
  - Logs memory_created events after successful commit

Migration 011 (keyword expansion):
  - One additional Ollama call per synthesis batch to generate search_keywords

Migration 012 (integrity upgrade):
  - _detect_source_class(): 6-value classification at write time only
  - Poisoning gate: quarantine lane (log event, don't create memory)
  - base_trust computed from tier ceiling + source_class adjustment at creation
  - required_roots: tier 5 memories need 2 independent roots
  - belief_state: 'active' on creation (or 'quarantined' for poisoned content)
"""

import hashlib
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..database import db_session
from ..models import EmbeddingQueue, Memory, MemoryLink, MemoryProvenance, Message, Segment
from ..services.ollama_client import generate_json, is_available as _ollama_ok

logger = logging.getLogger(__name__)

MAX_FACTS_PER_SEGMENT = 25
WINDOW_SIZE = 3000
WINDOW_OVERLAP = 200

# Derivation tier confidence ceilings
_TIER_CEILING = {1: 0.95, 2: 0.85, 3: 0.75, 4: 0.65, 5: 0.55}

# Migration 012: source-class trust adjustments (write-time only, NOT retrieval)
_TRUST_ADJUST = {
    "first_person":       +0.05,
    "third_person":       -0.08,
    "system_observed":     0.00,
    "assistant_generated": -0.10,
    "external_document":  -0.10,
    "unknown":            -0.05,
}

# First-person pronouns that indicate the user is the subject of the claim
_FIRST_PERSON_RE = re.compile(
    r"\b(I|I'[mv]|we|we're|my|our|mine|ours)\b", re.IGNORECASE
)


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


def _build_keywords_prompt(facts: List[str]) -> str:
    """Generate search keyword expansions for a list of facts."""
    facts_json = "\n".join(f"{i+1}. {f}" for i, f in enumerate(facts))
    return f"""For each fact below, provide 3-7 search terms a user might use to find it.
Include synonyms, abbreviations, related concepts, and technical aliases.
Return a JSON array of arrays (one inner array per fact, same order).

Facts:
{facts_json}

Return ONLY a JSON array like: [["term1","term2"], ["term3","term4"], ...]
Each inner array corresponds to one fact in the same order.
""".strip()


def _preflight_ollama(segment_id: int) -> None:
    """Raise OllamaUnavailableError if Ollama is not reachable."""
    if not _ollama_ok():
        raise OllamaUnavailableError(
            f"Ollama unavailable — skipping synthesis for segment {segment_id}"
        )


def _detect_derivation_tier(fact_text: str) -> int:
    """
    Migration 009: detect first-person vs third-party claims.
    First-person (I/we/my/our) → tier 3 (llm_inference, ceiling 0.75).
    Third-party → tier 4 (llm_synthesis, ceiling 0.65).
    """
    if _FIRST_PERSON_RE.search(fact_text[:100]):
        return 3
    return 4


def _detect_source_class(
    message_role: str,
    source_type: Optional[str],
    fact_text: str,
    has_tool_backing: bool = False,
) -> str:
    """
    Migration 012: classify the epistemic source of a fact (6 values).

    Classification logic:
      user role + first-person pronouns  → first_person
      user role + no first-person         → third_person
      assistant role + tool-backed        → system_observed
      assistant role + plain text         → assistant_generated
      source_type == sqlite_memory        → external_document
      otherwise                          → unknown

    CRITICAL: plain assistant text is NOT system_observed. That would overtrust
    model output without tool backing. system_observed is reserved for facts
    derived from an actual tool call, database query, or sensor reading.

    Applied at write time ONLY. Must not be re-applied during retrieval scoring
    to avoid double-counting the trust adjustment.
    """
    if message_role == "user":
        if _FIRST_PERSON_RE.search(fact_text[:100]):
            return "first_person"
        return "third_person"
    elif message_role == "assistant":
        if has_tool_backing:
            return "system_observed"
        return "assistant_generated"
    elif source_type == "sqlite_memory":
        return "external_document"
    return "unknown"


def _compute_base_trust(tier: int, source_class: str) -> float:
    """
    Migration 012: compute stable base_trust from tier ceiling + source_class.
    This value is written once at creation and used as the foundation for
    all subsequent _recompute_memory() calls.
    """
    ceiling = _TIER_CEILING.get(tier, 0.65)
    adjustment = _TRUST_ADJUST.get(source_class, -0.05)
    return round(min(max(ceiling + adjustment, 0.0), 1.0), 4)


def _apply_tier_ceiling(confidence: float, tier: int) -> float:
    """Cap confidence at the derivation tier's ceiling."""
    ceiling = _TIER_CEILING.get(tier, 0.65)
    return min(float(confidence), ceiling)


def _fetch_keywords_for_facts(facts: List[str]) -> List[List[str]]:
    """
    Migration 011: fetch search keyword expansions for a batch of facts.
    Returns a list of keyword lists (one per fact). Falls back to empty lists on error.
    """
    if not facts:
        return []
    try:
        result = generate_json(_build_keywords_prompt(facts))
        # Expect [[...], [...], ...]
        if isinstance(result, list) and len(result) == len(facts):
            out = []
            for item in result:
                if isinstance(item, list):
                    out.append([str(kw).lower().strip() for kw in item if kw])
                else:
                    out.append([])
            return out
    except Exception as e:
        logger.debug("Keyword generation failed (non-fatal): %s", e)
    return [[] for _ in facts]


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

        # Migration 007: use first message's sent_at as valid_from (world-event time)
        # This captures WHEN the fact was true, not when the system learned it.
        first_message_time = next(
            (m.sent_at for m in messages if m.sent_at is not None), None
        )

        # Get source_id and source_type from conversation
        from ..models import Conversation, Source as _Source
        conv = db.query(Conversation).get(seg.conversation_id)
        source_id = conv.source_id if conv else None
        source_type: Optional[str] = None
        if source_id:
            _src = db.query(_Source).get(source_id)
            source_type = _src.source_type if _src else None

        # Windowed synthesis: process full content in 3000-char windows
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

        # Cap at 25
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

        # Migration 011: generate keyword expansions for all facts in one batch call
        fact_texts = [fd.get("fact", "").strip() for fd in all_facts if fd.get("fact", "").strip()]
        try:
            keywords_by_fact = _fetch_keywords_for_facts(fact_texts)
        except Exception:
            keywords_by_fact = [[] for _ in fact_texts]
        # Build index from fact_text → keywords for fast lookup
        keywords_map: Dict[str, List[str]] = {}
        for ft, kws in zip(fact_texts, keywords_by_fact):
            keywords_map[ft] = kws

        # Migration 012: poisoning assessor (imported here to keep top-level imports clean)
        from ..services.poisoning import assess_poisoning
        from ..services.event_log import append_event as _append_event

        # Determine primary message role for source_class detection
        # Use the majority role in the segment (user > assistant > system)
        user_msg_count = sum(1 for m in messages if m.role == "user")
        primary_role = "user" if user_msg_count >= len(messages) // 2 else "assistant"

        created = 0
        quarantined_count = 0
        new_memory_ids: List[int] = []
        ingested_now = datetime.utcnow()

        for fact_data in all_facts:
            fact_text = fact_data.get("fact", "").strip()
            if not fact_text or len(fact_text) < 10:
                continue

            # Migration 012: poisoning gate — quarantine lane
            poison = assess_poisoning(fact_text)
            if poison.should_quarantine:
                import hashlib as _hashlib
                content_hash = _hashlib.sha256(fact_text.encode()).hexdigest()[:16]
                dedupe_key = f"memory_quarantined:segment:{segment_id}:{content_hash}"
                _append_event(
                    "memory_quarantined",
                    "segment",
                    segment_id,
                    {
                        "fact_preview": fact_text[:200],
                        "poison_score": poison.score,
                        "reasons": poison.reasons,
                        "segment_id": segment_id,
                    },
                    dedupe_key=dedupe_key,
                )
                logger.warning(
                    "Poisoning quarantine: segment %d fact score=%.2f reasons=%s",
                    segment_id, poison.score, poison.reasons,
                )
                quarantined_count += 1
                continue  # DO NOT create memory row

            fhash = _fact_hash(fact_text)

            # Normalize category
            raw_cat = fact_data.get("category", "other").lower().replace(" ", "_").strip()
            if raw_cat.endswith("s") and raw_cat[:-1] in VALID_CATEGORIES:
                raw_cat = raw_cat[:-1]
            category = raw_cat if raw_cat in VALID_CATEGORIES else "other"

            # Dedup check
            existing_mem = db.query(Memory).filter(Memory.fact_hash == fhash).first()
            if existing_mem:
                # Still link provenance to this segment
                prov = MemoryProvenance(
                    memory_id=existing_mem.id,
                    segment_id=segment_id,
                    source_id=source_id,
                    derivation_type="extracted",
                )
                db.add(prov)
                continue

            # Migration 007+009: derivation tier and confidence ceiling
            tier = _detect_derivation_tier(fact_text)
            raw_confidence = float(fact_data.get("confidence", 0.7))
            confidence = _apply_tier_ceiling(raw_confidence, tier)

            # Migration 012: source_class (write time only — NOT re-applied at retrieval)
            sc = _detect_source_class(primary_role, source_type, fact_text)
            base_trust = _compute_base_trust(tier, sc)
            # T5 memories (very low trust) require 2 independent roots for quorum
            required_roots = 2 if tier >= 5 else 1

            memory = Memory(
                fact=fact_text,
                fact_hash=fhash,
                category=category,
                confidence=confidence,
                importance=int(fact_data.get("importance", 3)),
                access_count=0,
                # Migration 007: bitemporal columns
                source_id=source_id,
                derivation_tier=tier,
                valid_from=first_message_time,
                ingested_at=ingested_now,
                corroboration_count=1,
                # Migration 011: keyword expansion
                search_keywords=keywords_map.get(fact_text, []),
                # Migration 012: integrity upgrade
                source_class=sc,
                base_trust=base_trust,
                required_roots=required_roots,
                belief_state="active",
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
            new_memory_ids.append(memory.id)
            created += 1

        db.flush()

        if quarantined_count:
            logger.info(
                "synthesize_memories_for_segment %d: created=%d quarantined=%d",
                segment_id, created, quarantined_count,
            )

    # Migration 010: log memory_created events (outside the db_session to avoid nesting)
    _log_memory_created_batch(new_memory_ids)

    return created


def _log_memory_created_batch(memory_ids: List[int]) -> None:
    """Log memory_created events for newly created memories (Migration 010)."""
    if not memory_ids:
        return
    try:
        from ..services.event_log import append_event
        for mid in memory_ids:
            append_event("memory_created", "memory", mid, {"memory_id": mid})
    except Exception as e:
        logger.debug("Event log for memory_created batch failed (non-fatal): %s", e)


# NOTE: _check_and_handle_contradictions removed from this module (Migration 007).
# Contradiction detection is now triggered by the embedding worker after embeddings exist.
# See app/tasks/pipeline_tasks.py: check_contradictions_batch()


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
    ingested_now = datetime.utcnow()
    new_memory_ids: List[int] = []

    with db_session() as db:
        for item in journal_entries + knowledge_items:
            text_val = getattr(item, "text", None) or getattr(item, "content", "")
            if not text_val or len(text_val) < 5:
                continue

            fhash = _fact_hash(text_val)
            if db.query(Memory).filter(Memory.fact_hash == fhash).first():
                continue

            memory = Memory(
                fact=text_val[:2000],
                fact_hash=fhash,
                category=getattr(item, "category", None) or getattr(item, "topic", "imported"),
                confidence=0.8,
                importance=3,
                source_id=source_id,
                derivation_tier=4,
                ingested_at=ingested_now,
                corroboration_count=1,
                search_keywords=[],
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
            new_memory_ids.append(memory.id)
            created += 1

        db.flush()

    _log_memory_created_batch(new_memory_ids)
    return created

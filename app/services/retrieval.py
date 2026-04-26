"""
3-tier retrieval router.

Tier 1: Structured SQL (tags, entities, date ranges)  → target <10ms
Tier 2: Memory fact search (trigram + FTS + provenance JOIN) → target <50ms
Tier 3: Semantic vector search (pgvector cosine)       → target <500ms

Phase 3: Added FTS tier (tier2_fts) using PostgreSQL tsvector/GIN — catches stemmed
         matches trigram misses. Both trigram and FTS fed into RRF fusion.
Phase 4: Entity-boosted retrieval, segment embedding search, MemoryLinks expansion,
         date filtering implemented.
Phase 5: Temporal filter — only returns memories with valid_until IS NULL by default.
         Pass include_superseded=True to see invalidated facts.

Every result includes a provenance chain: memory → segment → messages → raw source.
"""

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import db_session, engine, tenant_connection
from ..models import Memory, MemoryProvenance, Message, Segment, Source, Tag, TagAxis, EventLog
from ..schemas import ProvenanceChain, SearchResult, SearchResponse

logger = logging.getLogger(__name__)

SCHEMA = settings.MW_DB_SCHEMA

# ---------------------------------------------------------------------------
# Sentence-transformers singleton — shared across all Tier 3 calls
# ---------------------------------------------------------------------------
_embed_model = None


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(settings.MW_EMBED_MODEL)
    return _embed_model


def warmup_model():
    """Preload the embedding model so the first Tier 3 search is fast."""
    import numpy as np
    model = _get_embed_model()
    model.encode(["warmup"], normalize_embeddings=True)
    return model


def _build_provenance(memory_id: int, db: Session) -> List[ProvenanceChain]:
    provs = (
        db.query(MemoryProvenance)
        .filter(MemoryProvenance.memory_id == memory_id)
        .all()
    )
    result = []
    for p in provs:
        chain = ProvenanceChain(
            memory_id=memory_id,
            segment_id=p.segment_id,
            message_id=p.message_id,
            source_id=p.source_id,
            derivation_type=p.derivation_type,
        )
        if p.source_id:
            src = db.query(Source).get(p.source_id)
            if src:
                chain.source_path = src.source_path
        if p.message_id:
            msg = db.query(Message).get(p.message_id)
            if msg:
                chain.char_offset_start = msg.char_offset_start
                chain.char_offset_end = msg.char_offset_end
        result.append(chain)
    return result


# ---------------------------------------------------------------------------
# Phase 4a: Entity extraction from query text (for entity-boosted retrieval)
# ---------------------------------------------------------------------------

_ENTITY_PATTERNS = [
    r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',   # IPv4
    r'\b[A-Z][a-z]{2,}-\d+\b',                      # Spark-1, Spark-2 etc.
    r'\b[A-Z]{2,}[a-z]*(?:[A-Z][a-z]*)+\b',         # CamelCase project names
    r'\b[A-Z][A-Z0-9_]{2,}\b',                       # ALL_CAPS identifiers
    r'\bport\s+(\d{2,5})\b',                          # port 5432
]


def _extract_query_entities(query: str) -> List[str]:
    """Extract potential entity names from a query string."""
    entities = set()
    for pattern in _ENTITY_PATTERNS:
        for match in re.finditer(pattern, query, re.IGNORECASE):
            entities.add(match.group(0).strip())
    # Also extract capitalized words 3+ chars as potential project/system names
    for word in query.split():
        clean = re.sub(r'[^a-zA-Z0-9_-]', '', word)
        if len(clean) >= 3 and clean[0].isupper():
            entities.add(clean)
    return list(entities)


def _entity_boost(
    results: List[SearchResult],
    query: str,
    include_tombstoned: bool,
    include_superseded: bool,
    db: Session,
) -> List[SearchResult]:
    """
    Phase 4a: Boost memories that mention query entities.
    Finds Entity → EntityMention → segment_id → MemoryProvenance → memory_id.
    Adds +0.15 to RRF score for matched memories.
    """
    entity_names = _extract_query_entities(query)
    if not entity_names:
        return results

    boosted_ids: Set[int] = set()
    try:
        placeholders = ", ".join(f":e{i}" for i in range(len(entity_names)))
        params = {f"e{i}": name.lower() for i, name in enumerate(entity_names)}
        params["schema"] = SCHEMA

        with tenant_connection() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT DISTINCT mp.memory_id
                    FROM {SCHEMA}.entities e
                    JOIN {SCHEMA}.entity_mentions em ON em.entity_id = e.id
                    JOIN {SCHEMA}.memory_provenance mp ON mp.segment_id = em.segment_id
                    JOIN {SCHEMA}.memories m ON m.id = mp.memory_id
                    WHERE lower(e.canonical_name) IN ({placeholders})
                      AND m.tombstoned_at IS NULL
                """),
                params,
            ).fetchall()
        boosted_ids = {r[0] for r in rows}
    except Exception as e:
        logger.debug("Entity boost lookup failed: %s", e)
        return results

    if not boosted_ids:
        return results

    # Apply boost to existing results
    result_map = {r.id: r for r in results}
    for r in results:
        if r.id in boosted_ids:
            r.score = round(r.score + 0.15, 4)

    # Fetch any entity-matched memories NOT already in results
    already = {r.id for r in results}
    missing = boosted_ids - already
    if missing:
        with db_session() as db2:
            for mem_id in list(missing)[:5]:  # cap additions
                mem = db2.query(Memory).get(mem_id)
                if not mem or mem.tombstoned_at:
                    continue
                if not include_superseded and mem.valid_until is not None:
                    continue
                provenance = _build_provenance(mem_id, db2)
                results.append(SearchResult(
                    result_type="memory",
                    id=mem_id,
                    content=mem.fact,
                    score=0.15,  # entity boost only
                    tier=1,
                    provenance=provenance,
                    tombstoned=mem.tombstoned_at is not None,
                ))

    return results


# ---------------------------------------------------------------------------
# Tier 1: Structured SQL
# ---------------------------------------------------------------------------

def tier1_structured(
    db: Session,
    query: str,
    filters: Optional[Dict[str, Any]] = None,
    include_tombstoned: bool = False,
    include_superseded: bool = False,
    k: int = 10,
) -> List[SearchResult]:
    """Search by tags, entities, date ranges, conversation ID."""
    results = []
    filters = filters or {}

    base_q = db.query(Memory)
    if not include_tombstoned:
        base_q = base_q.filter(Memory.tombstoned_at.is_(None))
    # Phase 5b: temporal filter — only current facts by default
    if not include_superseded:
        base_q = base_q.filter(Memory.valid_until.is_(None))

    # Tag filters: {"domain": "infrastructure", "project": "imds-autoqa"}
    tag_filters = {k: v for k, v in filters.items() if k in ("domain", "intent", "sensitivity", "importance", "project")}
    if tag_filters:
        for axis_name, axis_value in tag_filters.items():
            axis = db.query(TagAxis).filter(TagAxis.axis_name == axis_name).first()
            if axis:
                seg_ids = [
                    t.segment_id for t in db.query(Tag)
                    .filter(Tag.axis_id == axis.id, Tag.value == axis_value)
                    .all()
                ]
                if seg_ids:
                    mem_ids = [
                        p.memory_id for p in db.query(MemoryProvenance)
                        .filter(MemoryProvenance.segment_id.in_(seg_ids))
                        .all()
                    ]
                    base_q = base_q.filter(Memory.id.in_(mem_ids))

    # Phase 4d: Date filters (was `pass` before)
    if "date_from" in filters or "date_to" in filters:
        try:
            date_from = filters.get("date_from")
            date_to = filters.get("date_to")
            # Find memory IDs via provenance → message → sent_at
            date_sql_parts = ["mp.memory_id IS NOT NULL"]
            date_params: Dict[str, Any] = {}
            if date_from:
                date_sql_parts.append("msg.sent_at >= :date_from")
                date_params["date_from"] = date_from
            if date_to:
                date_sql_parts.append("msg.sent_at <= :date_to")
                date_params["date_to"] = date_to

            with tenant_connection() as conn:
                rows = conn.execute(
                    text(f"""
                        SELECT DISTINCT mp.memory_id
                        FROM {SCHEMA}.memory_provenance mp
                        JOIN {SCHEMA}.messages msg ON msg.id = mp.message_id
                        WHERE {' AND '.join(date_sql_parts)}
                    """),
                    date_params,
                ).fetchall()
            dated_ids = [r[0] for r in rows]
            if dated_ids:
                base_q = base_q.filter(Memory.id.in_(dated_ids))
        except Exception as e:
            logger.debug("Date filter failed: %s", e)

    # Category filter
    if "category" in filters:
        base_q = base_q.filter(Memory.category == filters["category"])

    # Importance filter
    if "min_importance" in filters:
        base_q = base_q.filter(Memory.importance >= int(filters["min_importance"]))

    memories = base_q.order_by(Memory.utility_score.desc(), Memory.importance.desc()).limit(k).all()

    for mem in memories:
        tags = []
        for prov in mem.provenance:
            if prov.segment:
                for tag in prov.segment.tags:
                    tags.append({"axis": tag.axis.axis_name if tag.axis else "", "value": tag.value, "confidence": tag.confidence})

        provenance = _build_provenance(mem.id, db)

        results.append(SearchResult(
            result_type="memory",
            id=mem.id,
            content=mem.fact,
            score=1.0 if tag_filters else 0.8,
            tier=1,
            tags=tags,
            provenance=provenance,
            tombstoned=mem.tombstoned_at is not None,
        ))

    return results


# ---------------------------------------------------------------------------
# Tier 2: Memory trigram search
# ---------------------------------------------------------------------------

def tier2_trigram(
    db: Session,
    query: str,
    include_tombstoned: bool = False,
    include_superseded: bool = False,
    k: int = 10,
) -> List[SearchResult]:
    """Trigram similarity search on memory facts."""
    tomb_filter = "" if include_tombstoned else "AND m.tombstoned_at IS NULL"
    # Phase 5b: temporal filter
    temporal_filter = "" if include_superseded else "AND m.valid_until IS NULL"
    sql = text(f"""
        SELECT m.id, m.fact, m.category, m.confidence, m.tombstoned_at,
               similarity(m.fact, :query) AS sim
        FROM {SCHEMA}.memories m
        WHERE similarity(m.fact, :query) > 0.1
        {tomb_filter}
        {temporal_filter}
        ORDER BY sim DESC
        LIMIT :k
    """)

    with tenant_connection() as conn:
        rows = conn.execute(sql, {"query": query, "k": k}).fetchall()

    results = []
    with db_session() as db2:
        for row in rows:
            mem_id, fact, category, confidence, tomb, sim = row
            provenance = _build_provenance(mem_id, db2)
            results.append(SearchResult(
                result_type="memory",
                id=mem_id,
                content=fact,
                score=float(sim),
                tier=2,
                provenance=provenance,
                tombstoned=tomb is not None,
            ))

    return results


# ---------------------------------------------------------------------------
# Tier 2b: Full-text search (Phase 3)
# ---------------------------------------------------------------------------

def tier2_fts(
    query: str,
    include_tombstoned: bool = False,
    include_superseded: bool = False,
    k: int = 10,
) -> List[SearchResult]:
    """
    PostgreSQL full-text search using tsvector GIN index.
    Requires migration 005 (fact_tsv GENERATED ALWAYS AS column).

    Catches stemmed matches that trigram misses:
    e.g. "configuration" matches "configured", "GPU" matches "GPUs"
    """
    tomb_filter = "" if include_tombstoned else "AND m.tombstoned_at IS NULL"
    temporal_filter = "" if include_superseded else "AND m.valid_until IS NULL"
    sql = text(f"""
        SELECT m.id, m.fact, m.category, m.tombstoned_at,
               ts_rank_cd(m.fact_tsv, plainto_tsquery('english', :query)) AS score
        FROM {SCHEMA}.memories m
        WHERE m.fact_tsv @@ plainto_tsquery('english', :query)
        {tomb_filter}
        {temporal_filter}
        ORDER BY score DESC
        LIMIT :k
    """)

    try:
        with tenant_connection() as conn:
            rows = conn.execute(sql, {"query": query, "k": k}).fetchall()
    except Exception as e:
        # FTS columns not yet created (migration 005 not applied)
        logger.debug("FTS tier skipped (migration 005 not applied?): %s", e)
        return []

    results = []
    with db_session() as db2:
        for row in rows:
            mem_id, fact, category, tomb, score = row
            provenance = _build_provenance(mem_id, db2)
            results.append(SearchResult(
                result_type="memory",
                id=mem_id,
                content=fact,
                score=float(score),
                tier=2,
                provenance=provenance,
                tombstoned=tomb is not None,
            ))

    return results


# ---------------------------------------------------------------------------
# Tier 3: Semantic vector search (memories + segments)
# ---------------------------------------------------------------------------

def tier3_semantic(
    query: str,
    include_tombstoned: bool = False,
    include_superseded: bool = False,
    k: int = 10,
) -> List[SearchResult]:
    """
    Cosine similarity search via pgvector.

    Phase 4b: Also searches segment embeddings, then follows MemoryProvenance
    to find memories. This catches cases where the memory fact is too compressed
    but the richer segment summary matches.
    """
    import numpy as np

    model = _get_embed_model()
    qvec = model.encode([query], normalize_embeddings=True).astype(np.float32)[0].tolist()

    tomb_filter = "" if include_tombstoned else "AND m.tombstoned_at IS NULL"
    temporal_filter = "" if include_superseded else "AND m.valid_until IS NULL"

    # Primary: search memory embeddings directly
    sql_memory = text(f"""
        SELECT e.target_id, m.fact, m.category, m.tombstoned_at,
               1 - (e.vector <=> CAST(:qvec AS vector)) AS score
        FROM {SCHEMA}.embeddings e
        JOIN {SCHEMA}.memories m ON m.id = e.target_id
        WHERE e.target_type = 'memory'
        {tomb_filter}
        {temporal_filter}
        ORDER BY e.vector <=> CAST(:qvec AS vector)
        LIMIT :k
    """)

    # Phase 4b: also search segment embeddings → find memories via provenance
    sql_segment = text(f"""
        SELECT mp.memory_id, m.fact, m.category, m.tombstoned_at,
               1 - (e.vector <=> CAST(:qvec AS vector)) AS score
        FROM {SCHEMA}.embeddings e
        JOIN {SCHEMA}.segments s ON s.id = e.target_id
        JOIN {SCHEMA}.memory_provenance mp ON mp.segment_id = s.id
        JOIN {SCHEMA}.memories m ON m.id = mp.memory_id
        WHERE e.target_type = 'segment'
          AND s.tombstoned_at IS NULL
        {tomb_filter.replace('m.tombstoned_at', 'm.tombstoned_at')}
        {temporal_filter}
        ORDER BY e.vector <=> CAST(:qvec AS vector)
        LIMIT :k
    """)

    params = {"qvec": str(qvec), "k": k}
    mem_rows = []
    seg_rows = []

    with tenant_connection() as conn:
        mem_rows = conn.execute(sql_memory, params).fetchall()
        try:
            seg_rows = conn.execute(sql_segment, params).fetchall()
        except Exception as e:
            logger.debug("Segment embedding search failed: %s", e)

    # Merge: prefer higher score when same memory appears in both
    score_map: Dict[int, float] = {}
    fact_map: Dict[int, tuple] = {}

    for mem_id, fact, category, tomb, score in mem_rows:
        if score > score_map.get(mem_id, -1):
            score_map[mem_id] = float(score)
            fact_map[mem_id] = (fact, category, tomb)

    for mem_id, fact, category, tomb, score in seg_rows:
        # Segment-derived score gets a slight penalty since it's indirect
        adjusted = float(score) * 0.9
        if adjusted > score_map.get(mem_id, -1):
            score_map[mem_id] = adjusted
            fact_map[mem_id] = (fact, category, tomb)

    # Sort by score and take top-k
    sorted_mems = sorted(score_map.items(), key=lambda x: x[1], reverse=True)[:k]

    results = []
    with db_session() as db:
        for mem_id, score in sorted_mems:
            fact, category, tomb = fact_map[mem_id]
            provenance = _build_provenance(mem_id, db)
            results.append(SearchResult(
                result_type="memory",
                id=mem_id,
                content=fact,
                score=round(score, 4),
                tier=3,
                provenance=provenance,
                tombstoned=tomb is not None,
            ))

    return results


# ---------------------------------------------------------------------------
# Migration 011: Tier 2c — keyword expansion search
# ---------------------------------------------------------------------------

def tier2_keywords(
    query: str,
    include_tombstoned: bool = False,
    include_superseded: bool = False,
    k: int = 10,
) -> List[SearchResult]:
    """
    GIN array overlap search on memories.search_keywords.
    Parses the query into individual lowercase terms and finds memories whose
    keyword arrays contain any of those terms.

    Catches queries that don't match the literal fact text but do match a synonym
    stored at write-time (e.g. "pg port" → PostgreSQL memories with keyword "pg").
    """
    terms = [w.lower().strip() for w in re.split(r'\W+', query) if len(w.strip()) >= 2]
    if not terms:
        return []

    tomb_filter = "" if include_tombstoned else "AND m.tombstoned_at IS NULL"
    temporal_filter = "" if include_superseded else "AND m.valid_until IS NULL"

    # Build an ANY() match: memory whose search_keywords overlap with query terms
    try:
        with tenant_connection() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT m.id, m.fact, m.category, m.tombstoned_at
                    FROM {SCHEMA}.memories m
                    WHERE m.search_keywords && CAST(:terms AS TEXT[])
                    {tomb_filter}
                    {temporal_filter}
                    ORDER BY array_length(m.search_keywords, 1) DESC
                    LIMIT :k
                """),
                {"terms": terms, "k": k},
            ).fetchall()
    except Exception as e:
        logger.debug("Keyword tier skipped (migration 011 not applied?): %s", e)
        return []

    results = []
    with db_session() as db2:
        for row in rows:
            mem_id, fact, category, tomb = row
            provenance = _build_provenance(mem_id, db2)
            results.append(SearchResult(
                result_type="memory",
                id=mem_id,
                content=fact,
                score=0.5,  # placeholder; RRF fusion will reweight
                tier=2,
                provenance=provenance,
                tombstoned=tomb is not None,
            ))

    return results


# ---------------------------------------------------------------------------
# Migration 009: trust-tier weighting + corroboration boost
# ---------------------------------------------------------------------------

# Trust multipliers by source trust_tier (T1=user_explicit → T5=external)
_TRUST_WEIGHT = {1: 1.0, 2: 0.85, 3: 0.75, 4: 0.65, 5: 0.55}


def _apply_trust_and_corroboration(
    results: List[SearchResult],
    db: Session,
) -> List[SearchResult]:
    """
    Migration 009: adjust RRF scores by:
    1. Source trust tier: T1 facts rank higher than T4 facts.
    2. Corroboration count: facts agreed on by multiple independent sources rank higher.
       +10% per additional corroborating source, capped at +50%.
    """
    try:
        for r in results:
            mem = db.query(Memory).get(r.id)
            if not mem:
                continue

            # Trust weighting via source.trust_tier
            trust_w = 1.0
            if mem.source_id:
                src = db.query(Source).get(mem.source_id)
                if src:
                    trust_w = _TRUST_WEIGHT.get(src.trust_tier or 4, 0.65)
            elif mem.derivation_tier is not None:
                # Fallback: use memory's own derivation_tier as a proxy
                trust_w = _TRUST_WEIGHT.get(mem.derivation_tier, 0.65)

            # Corroboration boost: +10% per extra source, capped at +50%
            extra = min((mem.corroboration_count or 1) - 1, 5)
            corroboration_mult = 1.0 + 0.1 * extra

            r.score = round(r.score * trust_w * corroboration_mult, 4)
    except Exception as e:
        logger.debug("Trust/corroboration weighting failed: %s", e)

    return results


# ---------------------------------------------------------------------------
# Phase 4c: MemoryLinks graph expansion
# ---------------------------------------------------------------------------

def _expand_by_links(
    results: List[SearchResult],
    include_superseded: bool,
    max_additions: int = 5,
) -> List[SearchResult]:
    """
    Phase 4c: After main search, expand by 1 hop via MemoryLink table.
    Adds linked memories with score = parent_score * 0.5.
    Cap at max_additions new results.
    """
    if not results:
        return results

    existing_ids = {r.id for r in results}
    additions = []

    try:
        parent_ids = [r.id for r in results[:10]]  # expand from top-10 only
        placeholders = ", ".join(str(pid) for pid in parent_ids)

        # Temporal filter for linked memories
        temporal_filter = "" if include_superseded else "AND m.valid_until IS NULL"

        with tenant_connection() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT DISTINCT
                        CASE WHEN ml.memory_id_a = ANY(ARRAY[{placeholders}])
                             THEN ml.memory_id_b ELSE ml.memory_id_a END AS linked_id,
                        ml.memory_id_a AS parent_a,
                        ml.memory_id_b AS parent_b,
                        ml.link_type,
                        ml.confidence
                    FROM {SCHEMA}.memory_links ml
                    JOIN {SCHEMA}.memories m ON m.id = (
                        CASE WHEN ml.memory_id_a = ANY(ARRAY[{placeholders}])
                             THEN ml.memory_id_b ELSE ml.memory_id_a END
                    )
                    WHERE (ml.memory_id_a = ANY(ARRAY[{placeholders}])
                        OR ml.memory_id_b = ANY(ARRAY[{placeholders}]))
                      AND ml.link_type IN ('related', 'supports')
                      AND m.tombstoned_at IS NULL
                      {temporal_filter}
                    LIMIT 20
                """),
            ).fetchall()

        # Score linked results at 50% of parent score
        result_score_map = {r.id: r.score for r in results}

        with db_session() as db:
            for row in rows:
                linked_id = row[0]
                parent_a, parent_b = row[1], row[2]
                if linked_id in existing_ids:
                    continue
                parent_id = parent_a if parent_a in result_score_map else parent_b
                parent_score = result_score_map.get(parent_id, 0.1)
                link_score = round(parent_score * 0.5, 4)

                mem = db.query(Memory).get(linked_id)
                if not mem or mem.tombstoned_at:
                    continue
                provenance = _build_provenance(linked_id, db)
                additions.append(SearchResult(
                    result_type="memory",
                    id=linked_id,
                    content=mem.fact,
                    score=link_score,
                    tier=1,
                    provenance=provenance,
                    tombstoned=False,
                ))
                existing_ids.add(linked_id)
                if len(additions) >= max_additions:
                    break

    except Exception as e:
        logger.debug("MemoryLinks expansion failed (table may be empty): %s", e)

    return results + additions


# ---------------------------------------------------------------------------
# Migration 012: Intent-based boost (predicate/keyword matching)
# ---------------------------------------------------------------------------

# Map query intent signals → memory predicates/keywords to match against.
# Keys are frozensets of signal words; values are predicate patterns to match
# against memory.category or memory.search_keywords.
# Tied to predicates/entity-types, NOT to fragile category name strings.
_INTENT_SIGNALS: List[tuple] = [
    (frozenset(["where", "live", "city", "move", "address", "location"]),
     frozenset(["location", "lives_in", "moved_to", "address", "city"])),
    (frozenset(["work", "job", "company", "employer", "role", "position"]),
     frozenset(["employer", "works_at", "role", "job", "company", "work"])),
    (frozenset(["favorite", "prefer", "like", "best", "prefer"]),
     frozenset(["preference", "favorite", "prefer", "like"])),
    (frozenset(["decide", "chose", "pick", "why", "reason", "rationale"]),
     frozenset(["decision", "rationale", "reason", "chose", "why"])),
    (frozenset(["fix", "bug", "error", "broke", "broken", "issue", "problem"]),
     frozenset(["problem", "solution", "debug", "fix", "bug", "error"])),
    (frozenset(["config", "setting", "configure", "setup", "install"]),
     frozenset(["configuration", "config", "setup", "install", "infrastructure"])),
    (frozenset(["port", "host", "ip", "address", "endpoint", "url"]),
     frozenset(["infrastructure", "configuration", "port", "host", "ip"])),
]

_INTENT_BOOST_SCORE = 0.10


def _intent_boost(
    results: List[SearchResult],
    query: str,
    db: Session,
) -> List[SearchResult]:
    """
    Migration 012: boost memories whose predicates/keywords match detected
    query intent signals.

    Tied to memory predicates (category field + search_keywords), NOT to fragile
    category name strings. Applied AFTER entity boost so intent boost compounds
    on top of entity boost for highly relevant memories.

    source_class is intentionally NOT used here — it was applied at write time
    to base_trust, and re-applying it at retrieval would double-count.
    """
    if not results:
        return results

    query_lower = query.lower()
    query_words = set(re.split(r"\W+", query_lower))

    # Determine which predicates to boost for this query
    boost_predicates: set = set()
    for signal_words, predicates in _INTENT_SIGNALS:
        if query_words & signal_words or any(sw in query_lower for sw in signal_words):
            boost_predicates.update(predicates)

    if not boost_predicates:
        return results

    try:
        for r in results:
            mem = db.query(Memory).get(r.id)
            if not mem:
                continue
            # Check category match
            if mem.category and mem.category.lower() in boost_predicates:
                r.score = round(r.score + _INTENT_BOOST_SCORE, 4)
                continue
            # Check search_keywords overlap
            if mem.search_keywords:
                kw_set = {kw.lower() for kw in mem.search_keywords}
                if kw_set & boost_predicates:
                    r.score = round(r.score + _INTENT_BOOST_SCORE, 4)
    except Exception as e:
        logger.debug("_intent_boost failed (non-fatal): %s", e)

    return results


# ---------------------------------------------------------------------------
# Retrieval router
# ---------------------------------------------------------------------------

def search(
    query: str,
    filters: Optional[Dict[str, Any]] = None,
    k: int = 10,
    include_tombstoned: bool = False,
    include_superseded: bool = False,
    min_tier: int = 1,
    force_tier: Optional[int] = None,
) -> SearchResponse:
    """
    Multi-tier retrieval router. Returns SearchResponse with provenance chains.

    Tiers:
      1 - Structured SQL (tags, entities, date ranges)
      2 - Trigram + FTS (both fed into RRF)
      3 - Semantic pgvector (memories + segment embeddings)

    Post-processing:
      - Entity boost (+0.15 for memories mentioning query entities)
      - MemoryLinks graph expansion (related memories at 0.5x parent score)

    include_superseded: if False (default), excludes facts invalidated by
                        contradiction detection (valid_until IS NOT NULL)
    force_tier: run ONLY that tier (for benchmarking)
    """
    start = time.monotonic()
    tiers_used = []
    all_results: List[SearchResult] = []
    seen_ids: Set[int] = set()

    def dedupe(results: List[SearchResult]) -> List[SearchResult]:
        out = []
        for r in results:
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                out.append(r)
        return out

    # force_tier: bypass the cascade and run exactly one tier
    if force_tier is not None:
        if force_tier == 1:
            with db_session() as db:
                results = tier1_structured(db, query, filters or {}, include_tombstoned, include_superseded, k)
            if results:
                tiers_used.append(1)
            all_results = results
        elif force_tier == 2:
            with db_session() as db:
                results = tier2_trigram(db, query, include_tombstoned, include_superseded, k)
            if results:
                tiers_used.append(2)
            all_results = results
        elif force_tier == 3:
            try:
                results = tier3_semantic(query, include_tombstoned, include_superseded, k)
                if results:
                    tiers_used.append(3)
                all_results = results
            except Exception as e:
                logger.warning("Tier 3 semantic search failed: %s", e)

        elapsed_ms = (time.monotonic() - start) * 1000
        return SearchResponse(
            query=query,
            total=len(all_results),
            results=all_results[:k],
            tiers_used=tiers_used,
            latency_ms=round(elapsed_ms, 2),
        )

    # Normal cascade mode
    # Tier 1: Structured (only meaningful when filters are present)
    has_filters = bool(filters)
    if min_tier <= 1 and has_filters:
        with db_session() as db:
            t1 = tier1_structured(db, query, filters, include_tombstoned, include_superseded, k)
        t1 = dedupe(t1)
        all_results.extend(t1)
        if t1:
            tiers_used.append(1)

    # Tier 2a: Trigram (fast, ~5ms)
    t2_results: List[SearchResult] = []
    if min_tier <= 2:
        with db_session() as db:
            t2_results = tier2_trigram(db, query, include_tombstoned, include_superseded, k)
        if t2_results:
            tiers_used.append(2)

    # Tier 2b: FTS (Phase 3 — catches stemmed matches trigram misses)
    t2_fts_results: List[SearchResult] = []
    if min_tier <= 2:
        t2_fts_results = tier2_fts(query, include_tombstoned, include_superseded, k)
        # Only log as new tier if FTS finds something trigram didn't
        if t2_fts_results and 2 not in tiers_used:
            tiers_used.append(2)

    # Tier 3: Semantic (16ms, best recall)
    t3_results: List[SearchResult] = []
    if min_tier <= 3:
        try:
            t3_results = tier3_semantic(query, include_tombstoned, include_superseded, k)
            if t3_results:
                tiers_used.append(3)
        except Exception as e:
            logger.warning("Tier 3 semantic search failed: %s", e)

    # Tier 2c: Keyword expansion (Migration 011)
    t2_kw_results: List[SearchResult] = []
    if min_tier <= 2:
        t2_kw_results = tier2_keywords(query, include_tombstoned, include_superseded, k)
        if t2_kw_results and 2 not in tiers_used:
            tiers_used.append(2)

    # RRF fusion: merge Tier 2 (trigram), Tier 2b (FTS), Tier 2c (keywords), Tier 3 (semantic)
    if t2_results or t2_fts_results or t2_kw_results or t3_results:
        RRF_K = 60
        rrf_scores: Dict[int, float] = {}
        best_result: Dict[int, SearchResult] = {}

        for rank, r in enumerate(t2_results):
            rrf_scores[r.id] = rrf_scores.get(r.id, 0.0) + 1.0 / (RRF_K + rank + 1)
            best_result[r.id] = r

        for rank, r in enumerate(t2_fts_results):
            rrf_scores[r.id] = rrf_scores.get(r.id, 0.0) + 1.0 / (RRF_K + rank + 1)
            if r.id not in best_result or r.score > best_result[r.id].score:
                best_result[r.id] = r

        for rank, r in enumerate(t2_kw_results):
            rrf_scores[r.id] = rrf_scores.get(r.id, 0.0) + 1.0 / (RRF_K + rank + 1)
            if r.id not in best_result or r.score > best_result[r.id].score:
                best_result[r.id] = r

        for rank, r in enumerate(t3_results):
            rrf_scores[r.id] = rrf_scores.get(r.id, 0.0) + 1.0 / (RRF_K + rank + 1)
            # Prefer Tier 3 result (higher-quality semantic score) when seen in multiple tiers
            if r.id not in best_result or r.score > best_result[r.id].score:
                best_result[r.id] = r

        fused = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        for mem_id, rrf_score in fused:
            r = best_result[mem_id]
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                r.score = round(rrf_score, 4)
                all_results.append(r)

    # T1 results were already appended above; re-sort so high-importance
    # structured hits can still outrank fused results when score=1.0
    all_results.sort(key=lambda r: r.score, reverse=True)

    # Migration 009: trust-tier + corroboration boost (before entity boost so entity boost compounds on top)
    if all_results:
        with db_session() as db:
            all_results = _apply_trust_and_corroboration(all_results, db)
        all_results.sort(key=lambda r: r.score, reverse=True)

    # Phase 4a: Entity-boosted retrieval
    if all_results:
        with db_session() as db:
            all_results = _entity_boost(all_results, query, include_tombstoned, include_superseded, db)
        all_results.sort(key=lambda r: r.score, reverse=True)

    # Phase 4c: MemoryLinks graph expansion (best-effort)
    all_results = _expand_by_links(all_results, include_superseded)
    all_results.sort(key=lambda r: r.score, reverse=True)

    # Migration 012: intent-based boost (predicate/keyword matching, not category strings)
    if all_results:
        with db_session() as db:
            all_results = _intent_boost(all_results, query, db)
        all_results.sort(key=lambda r: r.score, reverse=True)

    # Update access + retrieval counts
    if all_results:
        with db_session() as db:
            for r in all_results[:k]:
                mem = db.query(Memory).get(r.id)
                if mem:
                    mem.access_count = (mem.access_count or 0) + 1
                    mem.retrieval_count = (mem.retrieval_count or 0) + 1
                    mem.last_accessed_at = datetime.utcnow()
                    # Cold-start: importance dominates until retrieval data accumulates
                    rc = mem.retrieval_count or 1
                    hc = mem.helpful_count or 0
                    imp_score = ((mem.importance or 3) - 1) / 4.0  # 0..1
                    if rc <= 5:
                        mem.utility_score = round(0.3 * (hc + 1) / (rc + 2) + 0.7 * imp_score, 4)
                    else:
                        mem.utility_score = round(0.7 * (hc + 1) / (rc + 2) + 0.3 * imp_score, 4)

    # Migration 012: record answer certificate (non-fatal — never blocks search)
    try:
        from .memory_integrity import record_answer_certificate
        result_memory_ids = [r.id for r in all_results[:k]]
        all_source_ids: set = set()
        for r in all_results[:k]:
            for prov in r.provenance:
                if prov.source_id:
                    all_source_ids.add(prov.source_id)
        if result_memory_ids:
            with db_session() as cert_db:
                record_answer_certificate(
                    query_text=query,
                    answer_text=None,
                    memory_ids=result_memory_ids,
                    source_ids=list(all_source_ids),
                    db=cert_db,
                )
    except Exception as _cert_err:
        logger.debug("Answer certificate recording failed (non-fatal): %s", _cert_err)

    elapsed_ms = (time.monotonic() - start) * 1000

    return SearchResponse(
        query=query,
        total=len(all_results),
        results=all_results[:k],
        tiers_used=tiers_used,
        latency_ms=round(elapsed_ms, 2),
    )

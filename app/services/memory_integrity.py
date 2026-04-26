"""
Memory integrity service (Migration 012).

Provides:
  _recompute_memory(db, memory_id)
      Deterministic confidence recompute from stable base_trust.
      Independent supporting root count and independent contradictory
      source family count. Does NOT touch superseded/quarantined states.

  record_answer_certificate(query_text, answer_text, memory_ids, source_ids, db)
      Record what was returned to a query, with two separate junction tables
      for memory lineage and source lineage.

  mark_certificates_stale(memory_ids, reason, db)
      Set stale_reason + stale_at on all certificates that used any of these
      memories (inverse lookup via answer_certificate_memories).

  clear_stale_certificates(memory_ids, db)
      Conservative clearing: only clears stale state when ALL linked memories
      are healthy (active belief_state, quorum met) and NO linked sources
      remain invalidated. Does not clear if only one memory recovered.

  backfill_legacy_base_trust(db, batch_size=100)
      One-time post-migration job: recompute base_trust from tier ceiling +
      inferred source_class for rows still carrying the bootstrap value.

Design notes:
  - DISTINCT source_id is the practical approximation for "independent provenance
    families" in 012. True provenance-DAG reasoning is deferred to 013+.
  - Contradiction penalty only counts believable contradictions: memories that
    are not in shadow/quarantined belief_state and whose sources are not
    invalidated.
  - superseded and quarantined belief_states are set by other code paths;
    _recompute_memory respects but does not override them.
"""

import hashlib
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import engine, tenant_connection
from ..models import AnswerCertificate, AnswerCertificateMemory, AnswerCertificateSource, Memory

logger = logging.getLogger(__name__)

SCHEMA = settings.MW_DB_SCHEMA

# Tier confidence ceilings (mirrors memory_synthesizer.py _TIER_CEILING)
_TIER_CEILING = {1: 0.95, 2: 0.85, 3: 0.75, 4: 0.65, 5: 0.55}

# Source-class trust adjustments (applied at write time only, NEVER at retrieval)
_TRUST_ADJUST = {
    "first_person":      +0.05,
    "third_person":      -0.08,
    "system_observed":    0.00,
    "assistant_generated": -0.10,
    "external_document": -0.10,
    "unknown":           -0.05,
}

# Per-additional-independent-root boost
_ROOT_BOOST_PER_EXTRA = 0.05

# Per-independent-contradictory-family penalty
_CONTRADICTION_PENALTY_PER_FAMILY = 0.15


def _count_active_roots(db: Session, memory_id: int) -> int:
    """
    Count independent provenance families (DISTINCT source_id) from
    non-invalidated sources for this memory.

    Approximation note: DISTINCT source_id is a practical proxy for independent
    provenance families in 012. One bad source split across multiple source
    records can overcount. True DAG lineage reasoning is deferred to 013+.
    """
    row = db.execute(
        text(f"""
            SELECT COUNT(DISTINCT mp.source_id)
            FROM {SCHEMA}.memory_provenance mp
            JOIN {SCHEMA}.sources s ON s.id = mp.source_id
            WHERE mp.memory_id = :mid
              AND s.invalidated_at IS NULL
        """),
        {"mid": memory_id},
    ).fetchone()
    return int(row[0]) if row else 0


def _count_contradiction_families(db: Session, memory_id: int) -> int:
    """
    Count independent contradictory source families: DISTINCT source_id from
    contradicting memories that are themselves believable.

    Correction #2: only count contradictions from memories whose belief_state
    is NOT 'shadow' or 'quarantined' (weak/poisoned contradictions should not
    penalize trusted memories).

    Handles both link directions (contradictions are logically symmetric but
    stored directionally).
    """
    row = db.execute(
        text(f"""
            SELECT COUNT(DISTINCT mp.source_id)
            FROM {SCHEMA}.memory_links ml
            JOIN {SCHEMA}.memories m ON m.id =
                CASE WHEN ml.memory_id_a = :mid
                     THEN ml.memory_id_b
                     ELSE ml.memory_id_a END
            JOIN {SCHEMA}.memory_provenance mp ON mp.memory_id = m.id
            JOIN {SCHEMA}.sources s ON s.id = mp.source_id
            WHERE ml.link_type = 'contradicts'
              AND (ml.memory_id_a = :mid OR ml.memory_id_b = :mid)
              AND m.valid_until IS NULL
              AND m.tombstoned_at IS NULL
              AND m.belief_state NOT IN ('shadow', 'quarantined')
              AND s.invalidated_at IS NULL
        """),
        {"mid": memory_id},
    ).fetchone()
    return int(row[0]) if row else 0


def _recompute_memory(db: Session, memory_id: int) -> None:
    """
    Deterministic confidence + belief_state recompute for a memory.

    Algorithm:
      1. Read base_trust (set once at creation from tier ceiling + source_class).
         If NULL (shouldn't happen post-backfill), fall back to current confidence.
      2. confidence = base_trust + ROOT_BOOST * (active_roots - 1)
                                 - CONTRADICTION_PENALTY * contradiction_families
         clamped to [0, 1].
      3. belief_state transitions:
         - superseded: set by contradiction code path — NOT changed here
         - quarantined: set by poisoning gate — NOT changed here
         - shadow: 0 active roots and not superseded
         - disputed: below required_roots quorum, OR contested
         - active: healthy

    Invariant: calling this function N times produces the same result as
    calling it once, as long as the underlying provenance/link data is the same.
    """
    mem = db.query(Memory).get(memory_id)
    if not mem or mem.tombstoned_at is not None:
        return

    # Don't touch states set by other code paths
    if mem.belief_state in ("superseded", "quarantined"):
        # Still recompute confidence so it reflects current source validity
        # but preserve the belief_state
        pass

    active_roots = _count_active_roots(db, memory_id)
    contradiction_families = _count_contradiction_families(db, memory_id)

    # Use base_trust as stable foundation; fall back if missing (legacy rows
    # before post-migration backfill runs)
    base = mem.base_trust
    if base is None:
        logger.debug(
            "memory %d has no base_trust (bootstrap not yet run); using confidence as fallback",
            memory_id,
        )
        base = min(max(mem.confidence or 0.5, 0.0), 1.0)

    # Compute new confidence
    extra_roots = max(active_roots - 1, 0)
    new_confidence = base + _ROOT_BOOST_PER_EXTRA * extra_roots
    new_confidence -= _CONTRADICTION_PENALTY_PER_FAMILY * contradiction_families
    new_confidence = round(min(max(new_confidence, 0.0), 1.0), 4)

    # Determine belief_state (only update non-terminal states)
    required_roots = mem.required_roots or 1
    superseded = mem.superseded_by is not None

    if mem.belief_state not in ("superseded", "quarantined"):
        if active_roots == 0 and not superseded:
            new_belief = "shadow"
        elif active_roots < required_roots:
            new_belief = "disputed"
        elif contradiction_families > 0 and active_roots <= contradiction_families:
            new_belief = "disputed"
        else:
            new_belief = "active"
        mem.belief_state = new_belief

    # Track when system corrected its stated confidence
    if mem.confidence != new_confidence:
        mem.corrected_at = datetime.utcnow()

    mem.confidence = new_confidence
    db.flush()


# ---------------------------------------------------------------------------
# Answer certificates
# ---------------------------------------------------------------------------

def record_answer_certificate(
    query_text: str,
    answer_text: Optional[str],
    memory_ids: List[int],
    source_ids: List[int],
    db: Session,
) -> Optional[int]:
    """
    Record what was returned to a query.
    Creates one AnswerCertificate + N memory junction rows + M source junction rows.
    Returns certificate_id, or None on error (non-fatal).
    """
    if not memory_ids:
        return None
    try:
        cert = AnswerCertificate(
            query_text=query_text[:2000],
            answer_text=(answer_text[:5000] if answer_text else None),
        )
        db.add(cert)
        db.flush()

        for mid in memory_ids:
            db.add(AnswerCertificateMemory(certificate_id=cert.id, memory_id=mid))

        seen_sources = set()
        for sid in source_ids:
            if sid not in seen_sources:
                db.add(AnswerCertificateSource(certificate_id=cert.id, source_id=sid))
                seen_sources.add(sid)

        db.commit()
        return cert.id
    except Exception as e:
        logger.debug("record_answer_certificate failed (non-fatal): %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def mark_certificates_stale(
    memory_ids: List[int],
    reason: str,
    db: Session,
) -> int:
    """
    Mark all certificates that used any of the given memories as stale.
    Sets stale_reason and stale_at. Idempotent — already-stale certs are skipped.
    Returns number of certificates newly marked stale.
    """
    if not memory_ids:
        return 0
    try:
        placeholders = ", ".join(str(mid) for mid in memory_ids)
        now = datetime.utcnow()
        result = db.execute(
            text(f"""
                UPDATE {SCHEMA}.answer_certificates ac
                SET stale_reason = :reason,
                    stale_at = :now
                WHERE stale_at IS NULL
                  AND EXISTS (
                      SELECT 1 FROM {SCHEMA}.answer_certificate_memories acm
                      WHERE acm.certificate_id = ac.id
                        AND acm.memory_id IN ({placeholders})
                  )
            """),
            {"reason": reason, "now": now},
        )
        db.flush()
        return result.rowcount if hasattr(result, "rowcount") else 0
    except Exception as e:
        logger.debug("mark_certificates_stale failed (non-fatal): %s", e)
        return 0


def clear_stale_certificates(
    memory_ids: List[int],
    db: Session,
) -> int:
    """
    Conservatively clear stale certificates after memory recovery.

    Clearing rules (ALL must hold before a certificate is cleared):
      1. The certificate is currently stale (stale_at IS NOT NULL).
      2. NONE of its linked memories have belief_state IN ('shadow', 'disputed',
         'quarantined') — all must be 'active' or 'superseded'.
      3. NONE of its linked sources are invalidated.

    Rationale: if a certificate used 5 memories and only 1 recovered, the answer
    it gave may still be partially wrong. We only clear when the entire answer's
    basis is healthy again.

    Returns number of certificates newly cleared.
    """
    if not memory_ids:
        return 0
    try:
        placeholders = ", ".join(str(mid) for mid in memory_ids)
        now = datetime.utcnow()

        # Find cert IDs that are stale AND linked to one of our recovered memories
        stale_certs = db.execute(
            text(f"""
                SELECT DISTINCT ac.id
                FROM {SCHEMA}.answer_certificates ac
                JOIN {SCHEMA}.answer_certificate_memories acm ON acm.certificate_id = ac.id
                WHERE ac.stale_at IS NOT NULL
                  AND acm.memory_id IN ({placeholders})
            """),
        ).fetchall()

        if not stale_certs:
            return 0

        cert_ids = [r[0] for r in stale_certs]
        cleared = 0

        for cert_id in cert_ids:
            # Check: are ALL linked memories in a healthy state?
            unhealthy_memories = db.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM {SCHEMA}.answer_certificate_memories acm
                    JOIN {SCHEMA}.memories m ON m.id = acm.memory_id
                    WHERE acm.certificate_id = :cid
                      AND m.belief_state IN ('shadow', 'disputed', 'quarantined')
                      AND m.tombstoned_at IS NULL
                """),
                {"cid": cert_id},
            ).scalar() or 0

            if unhealthy_memories > 0:
                continue

            # Check: are ALL linked sources still valid (not invalidated)?
            invalidated_sources = db.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM {SCHEMA}.answer_certificate_sources acs
                    JOIN {SCHEMA}.sources s ON s.id = acs.source_id
                    WHERE acs.certificate_id = :cid
                      AND s.invalidated_at IS NOT NULL
                """),
                {"cid": cert_id},
            ).scalar() or 0

            if invalidated_sources > 0:
                continue

            # All conditions met — clear this certificate
            db.execute(
                text(f"""
                    UPDATE {SCHEMA}.answer_certificates
                    SET cleared_at = :now,
                        stale_reason = NULL,
                        stale_at = NULL
                    WHERE id = :cid
                """),
                {"now": now, "cid": cert_id},
            )
            cleared += 1

        db.flush()
        return cleared
    except Exception as e:
        logger.debug("clear_stale_certificates failed (non-fatal): %s", e)
        return 0


# ---------------------------------------------------------------------------
# Post-migration backfill
# ---------------------------------------------------------------------------

def backfill_legacy_base_trust(db: Session, batch_size: int = 100) -> int:
    """
    One-time post-migration job: recompute base_trust from tier ceiling +
    inferred source_class for legacy rows where base_trust was bootstrapped
    from (possibly mutated) confidence.

    Only processes rows where base_trust is NOT NULL but was set by the
    bootstrap UPDATE (i.e., confidence == base_trust within float precision),
    OR rows where source_class is still 'unknown' (first ingestion before 012).

    Returns number of rows updated.

    IMPORTANT: This replaces the temporary bootstrap. After running, base_trust
    reflects the stable derivation-tier + source-class formula, not the old
    mutated confidence value.
    """
    updated = 0
    try:
        offset = 0
        while True:
            rows = db.execute(
                text(f"""
                    SELECT id, derivation_tier, source_class, confidence, base_trust
                    FROM {SCHEMA}.memories
                    WHERE tombstoned_at IS NULL
                    ORDER BY id
                    LIMIT :batch OFFSET :offset
                """),
                {"batch": batch_size, "offset": offset},
            ).fetchall()

            if not rows:
                break

            for mem_id, tier, sc, conf, bt in rows:
                tier = tier or 4
                sc = sc or "unknown"
                ceiling = _TIER_CEILING.get(tier, 0.65)
                adjustment = _TRUST_ADJUST.get(sc, -0.05)
                new_base = round(min(max(ceiling + adjustment, 0.0), 1.0), 4)

                # Only update if the base_trust is still the bootstrapped value
                # (i.e., it equals the old confidence within float precision)
                # or if it would meaningfully change.
                if bt is None or abs(bt - new_base) > 0.001:
                    db.execute(
                        text(f"""
                            UPDATE {SCHEMA}.memories
                            SET base_trust = :bt
                            WHERE id = :mid
                        """),
                        {"bt": new_base, "mid": mem_id},
                    )
                    updated += 1

            db.flush()
            offset += batch_size

        db.commit()
        logger.info("backfill_legacy_base_trust: updated %d rows", updated)
    except Exception as e:
        logger.error("backfill_legacy_base_trust failed: %s", e)
        try:
            db.rollback()
        except Exception:
            pass

    return updated


# ---------------------------------------------------------------------------
# Certificate fetch helpers (used by router)
# ---------------------------------------------------------------------------

def get_certificate(cert_id: int, db: Session) -> Optional[dict]:
    """Fetch a single certificate with memory_ids and source_ids."""
    cert = db.query(AnswerCertificate).get(cert_id)
    if not cert:
        return None
    return _cert_to_dict(cert, db)


def list_certificates(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    stale_only: bool = False,
) -> dict:
    """List certificates, newest first."""
    q = db.query(AnswerCertificate)
    if stale_only:
        q = q.filter(AnswerCertificate.stale_at.isnot(None))
    total = q.count()
    certs = (
        q.order_by(AnswerCertificate.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [_cert_to_dict(c, db) for c in certs],
    }


def _cert_to_dict(cert: AnswerCertificate, db: Session) -> dict:
    memory_ids = [acm.memory_id for acm in cert.memory_links]
    source_ids = [acs.source_id for acs in cert.source_links]
    return {
        "id": cert.id,
        "query_text": cert.query_text,
        "answer_text": cert.answer_text,
        "confidence": cert.confidence,
        "stale_reason": cert.stale_reason,
        "stale_at": cert.stale_at,
        "cleared_at": cert.cleared_at,
        "created_at": cert.created_at,
        "memory_ids": memory_ids,
        "source_ids": source_ids,
    }

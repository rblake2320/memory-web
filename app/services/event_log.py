"""
Append-only event log service (Migration 010).

Every significant memory mutation is recorded here with a chained SHA-256 hash.
The DB-level trigger prevents UPDATE/DELETE, making the log tamper-evident.

Usage:
    from ..services.event_log import append_event
    append_event("memory_created", "memory", memory_id, {"fact": "...", ...})

The hash chain: hash = SHA256(prev_hash + event_type + str(target_id) + json.dumps(payload))
"""

import hashlib
import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy import text

from ..config import settings
from ..database import engine, tenant_connection

logger = logging.getLogger(__name__)

SCHEMA = settings.MW_DB_SCHEMA
_GENESIS_HASH = "0" * 64  # sentinel for the very first event


def _compute_hash(prev_hash: Optional[str], event_type: str, target_id: int, payload: Dict[str, Any]) -> str:
    """SHA-256 of (prev_hash || event_type || str(target_id) || canonical_json(payload))."""
    raw = (prev_hash or _GENESIS_HASH) + event_type + str(target_id) + json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_last_hash() -> Optional[str]:
    """Fetch the most recent hash in the chain for the current tenant (for chaining new events)."""
    try:
        with tenant_connection() as conn:
            row = conn.execute(
                text(f"""
                    SELECT hash FROM {SCHEMA}.event_log
                    WHERE tenant_id = current_setting('app.current_tenant', true)::uuid
                    ORDER BY id DESC LIMIT 1
                """)
            ).fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.debug("Could not fetch last event_log hash: %s", e)
        return None


def _round_floats(obj: Any) -> Any:
    """
    Recursively round floats to 6 decimal places.

    C-1 fix: PostgreSQL JSONB normalises floating-point values at storage time
    (e.g. 0.8500000000000001 → 0.85). Python's json.dumps does not. If the hash
    is computed from the pre-storage Python float but verify_chain reads the
    JSONB-normalised value, the hashes diverge silently on every contradiction
    event that includes a similarity score. Rounding to 6 dp before hashing and
    before storage makes both sides deterministically identical.
    """
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v) for v in obj]
    return obj


def append_event(
    event_type: str,
    target_type: str,
    target_id: int,
    payload: Dict[str, Any],
    dedupe_key: Optional[str] = None,
) -> Optional[int]:
    """
    Append a new event to the immutable log.
    Returns the event_id, or None on failure (non-fatal — event log is best-effort).

    C-1 fix: floats are rounded to 6 dp before hashing so Python hash ==
    JSONB-read hash (see _round_floats above).

    C-5 fix: prev_hash read and INSERT are wrapped in a single transaction
    protected by pg_advisory_xact_lock so concurrent callers cannot both read
    the same prev_hash and fork the chain. The lock is per-tenant so different
    tenants can append concurrently without contention.

    Migration 012 (dedupe_key):
    When dedupe_key is provided, the function is idempotent: duplicate calls
    with the same key return the existing event_id without inserting a second row.
    The race-safe path uses ON CONFLICT DO NOTHING on the unique index.

    CRITICAL: The successful-insert dedupe path still acquires pg_advisory_xact_lock
    and reads prev_hash so new events maintain correct chain ordering.
    Only the conflict path (row already exists) skips hash computation and
    returns the existing row immediately.

    Callers build deterministic keys:
        f"{event_type}:{target_type}:{target_id}:{content_hash_16}"
    """
    try:
        # Normalise payload so hash is stable across JSONB round-trips
        clean_payload = _round_floats(payload)

        with tenant_connection() as conn:
            # Fast path: if dedupe_key is provided, check for existing row
            # before acquiring the advisory lock (avoids lock contention for
            # repeat calls on already-ingested events).
            if dedupe_key:
                existing = conn.execute(
                    text(f"SELECT id FROM {SCHEMA}.event_log WHERE dedupe_key = :dk"),
                    {"dk": dedupe_key},
                ).fetchone()
                if existing:
                    return existing[0]

            # Acquire a transaction-scoped advisory lock keyed to the current
            # tenant. Using hashtext(current_setting(...)) means different tenants
            # serialize independently — no cross-tenant lock contention.
            conn.execute(text(
                "SELECT pg_advisory_xact_lock(hashtext(current_setting('app.current_tenant', true)))"
            ))

            prev_row = conn.execute(
                text(f"""
                    SELECT hash FROM {SCHEMA}.event_log
                    WHERE tenant_id = current_setting('app.current_tenant', true)::uuid
                    ORDER BY id DESC LIMIT 1
                """)
            ).fetchone()
            prev_hash = prev_row[0] if prev_row else None

            new_hash = _compute_hash(prev_hash, event_type, target_id, clean_payload)

            if dedupe_key:
                # Race-safe insert: ON CONFLICT DO NOTHING handles the case where
                # another process inserted between our fast-path check and lock.
                row = conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.event_log
                            (event_type, target_type, target_id, payload, hash, prev_hash, dedupe_key)
                        VALUES
                            (:et, :tt, :tid, :payload, :hash, :prev_hash, :dk)
                        ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL
                        DO NOTHING
                        RETURNING id
                    """),
                    {
                        "et": event_type,
                        "tt": target_type,
                        "tid": target_id,
                        "payload": json.dumps(clean_payload),
                        "hash": new_hash,
                        "prev_hash": prev_hash,
                        "dk": dedupe_key,
                    },
                ).fetchone()
                conn.commit()
                if row:
                    return row[0]
                # Conflict: another process won the race — fetch existing row.
                # New transaction (advisory lock released on commit above).
                existing = conn.execute(
                    text(f"SELECT id FROM {SCHEMA}.event_log WHERE dedupe_key = :dk"),
                    {"dk": dedupe_key},
                ).fetchone()
                return existing[0] if existing else None
            else:
                # Normal path (no dedupe_key)
                row = conn.execute(
                    text(f"""
                        INSERT INTO {SCHEMA}.event_log
                            (event_type, target_type, target_id, payload, hash, prev_hash)
                        VALUES
                            (:et, :tt, :tid, :payload, :hash, :prev_hash)
                        RETURNING id
                    """),
                    {
                        "et": event_type,
                        "tt": target_type,
                        "tid": target_id,
                        "payload": json.dumps(clean_payload),
                        "hash": new_hash,
                        "prev_hash": prev_hash,
                    },
                ).fetchone()
                conn.commit()
                return row[0] if row else None
    except Exception as e:
        logger.warning("event_log append failed (non-fatal): %s", e)
        return None


def verify_chain(tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Walk the full hash chain and verify each link.
    Returns {valid: bool, chain_length: int, first_broken_at: int|None}

    If tenant_id is provided, only events for that tenant are verified.
    If omitted, uses the current tenant context (set via tenant_connection).

    O(n) scan — n grows slowly at current ingestion rate (~3 events/memory, ~1200 memories).
    """
    try:
        with tenant_connection(tenant_id) as conn:
            if tenant_id:
                rows = conn.execute(
                    text(f"""
                        SELECT id, event_type, target_id, payload, hash, prev_hash
                        FROM {SCHEMA}.event_log
                        WHERE tenant_id = :tid::uuid
                        ORDER BY id ASC
                    """),
                    {"tid": tenant_id},
                ).fetchall()
            else:
                rows = conn.execute(
                    text(f"""
                        SELECT id, event_type, target_id, payload, hash, prev_hash
                        FROM {SCHEMA}.event_log
                        WHERE tenant_id = current_setting('app.current_tenant', true)::uuid
                        ORDER BY id ASC
                    """)
                ).fetchall()
    except Exception as e:
        return {"valid": False, "chain_length": 0, "first_broken_at": None, "error": str(e)}

    if not rows:
        return {"valid": True, "chain_length": 0, "first_broken_at": None}

    prev_hash: Optional[str] = None
    for row in rows:
        event_id, event_type, target_id, payload_raw, stored_hash, stored_prev_hash = row

        # Verify the prev_hash pointer matches what we computed on the previous iteration
        if stored_prev_hash != prev_hash:
            return {"valid": False, "chain_length": len(rows), "first_broken_at": event_id}

        # Recompute the hash and compare.
        # psycopg2 returns JSONB as a Python dict; older rows stored as TEXT
        # (json.dumps string) need json.loads first. Either way, apply the same
        # float rounding that append_event uses so hashes match.
        try:
            if isinstance(payload_raw, str):
                payload_raw = json.loads(payload_raw)
        except Exception:
            payload_raw = {}
        payload = _round_floats(payload_raw)

        expected_hash = _compute_hash(prev_hash, event_type, target_id, payload)
        if expected_hash != stored_hash:
            return {"valid": False, "chain_length": len(rows), "first_broken_at": event_id}

        prev_hash = stored_hash

    return {"valid": True, "chain_length": len(rows), "first_broken_at": None}


def get_memory_history(memory_id: int):
    """Return all event_log entries for a specific memory, ordered chronologically."""
    try:
        with tenant_connection() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT id, event_type, target_type, target_id, payload, hash, prev_hash, created_at
                    FROM {SCHEMA}.event_log
                    WHERE target_type = 'memory' AND target_id = :mid
                    ORDER BY id ASC
                """),
                {"mid": memory_id},
            ).fetchall()
        return [
            {
                "id": r[0],
                "event_type": r[1],
                "target_type": r[2],
                "target_id": r[3],
                "payload": r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}"),
                "hash": r[5],
                "prev_hash": r[6],
                "created_at": r[7].isoformat() if r[7] else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("get_memory_history failed for %d: %s", memory_id, e)
        return []

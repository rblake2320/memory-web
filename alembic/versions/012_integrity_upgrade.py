"""Memory Integrity Upgrade

Migration 012:
Backports 8 architectural features from the "mem v2" prototype, corrected by
16 skeptic-review items:

A. Answer certificates (with two separate junction tables for memory/source lineage)
B. _recompute_memory(): deterministic confidence from stable base_trust
C. corrected_at: when system corrected its belief (vs valid_until = when fact became false)
D. required_roots: independent provenance family quorum (DISTINCT source_id)
E. source_class: 6-value classification, applied at write time only
F. Poisoning quarantine lane
G. Intent-based query boosting
H. Event-level idempotency (dedupe_key with ON CONFLICT DO NOTHING)

Implementation notes:
- CHECK constraints use DO/$$/ guards against pg_constraint catalog because
  PostgreSQL does not support ADD CONSTRAINT IF NOT EXISTS.
- base_trust bootstrap backfill is temporary; run backfill_legacy_base_trust()
  post-migration to recompute from tier ceiling + inferred source_class.
- All DDL uses op.execute() directly (not op.get_bind()), which is the correct
  Alembic pattern for raw SQL execution with transactional DDL.

Revision ID: 012
Revises: 011
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None

SCHEMA = "memoryweb"


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. New columns on memories
    # -----------------------------------------------------------------------
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories "
        f"ADD COLUMN IF NOT EXISTS corrected_at TIMESTAMPTZ"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories "
        f"ADD COLUMN IF NOT EXISTS required_roots SMALLINT NOT NULL DEFAULT 1"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories "
        f"ADD COLUMN IF NOT EXISTS source_class VARCHAR(30) NOT NULL DEFAULT 'unknown'"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories "
        f"ADD COLUMN IF NOT EXISTS belief_state VARCHAR(20) NOT NULL DEFAULT 'active'"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories "
        f"ADD COLUMN IF NOT EXISTS base_trust FLOAT"
    ))

    # -----------------------------------------------------------------------
    # 2. DB-level CHECK constraints (guarded against pg_constraint catalog).
    #    PostgreSQL does not support ADD CONSTRAINT IF NOT EXISTS, so we use
    #    anonymous PL/pgSQL DO blocks to check first.
    # -----------------------------------------------------------------------
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF NOT EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'chk_belief_state'\n"
        f"      AND conrelid = '{SCHEMA}.memories'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.memories ADD CONSTRAINT chk_belief_state\n"
        f"    CHECK (belief_state IN ('active','shadow','disputed','superseded','quarantined'));\n"
        f"  END IF;\n"
        f"END $body$;"
    ))
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF NOT EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'chk_source_class'\n"
        f"      AND conrelid = '{SCHEMA}.memories'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.memories ADD CONSTRAINT chk_source_class\n"
        f"    CHECK (source_class IN (\n"
        f"      'first_person','third_person','system_observed',\n"
        f"      'assistant_generated','external_document','unknown'));\n"
        f"  END IF;\n"
        f"END $body$;"
    ))
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF NOT EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'chk_required_roots_positive'\n"
        f"      AND conrelid = '{SCHEMA}.memories'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.memories ADD CONSTRAINT chk_required_roots_positive\n"
        f"    CHECK (required_roots > 0);\n"
        f"  END IF;\n"
        f"END $body$;"
    ))
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF NOT EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'chk_base_trust_range'\n"
        f"      AND conrelid = '{SCHEMA}.memories'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.memories ADD CONSTRAINT chk_base_trust_range\n"
        f"    CHECK (base_trust IS NULL OR base_trust BETWEEN 0 AND 1);\n"
        f"  END IF;\n"
        f"END $body$;"
    ))
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF NOT EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'chk_confidence_range'\n"
        f"      AND conrelid = '{SCHEMA}.memories'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.memories ADD CONSTRAINT chk_confidence_range\n"
        f"    CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1);\n"
        f"  END IF;\n"
        f"END $body$;"
    ))

    # -----------------------------------------------------------------------
    # 3. Temporary bootstrap backfill for base_trust.
    #    ACCEPTABLE MIGRATION SHORTCUT — NOT permanent truth for legacy rows.
    #    Run backfill_legacy_base_trust() post-migration to recompute from
    #    tier ceiling + inferred source_class for all existing rows.
    # -----------------------------------------------------------------------
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.memories\n"
        f"SET base_trust = LEAST(GREATEST(COALESCE(confidence, 0.5), 0), 1)\n"
        f"WHERE base_trust IS NULL"
    ))

    # Backfill belief_state for rows already in superseded state
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.memories\n"
        f"SET belief_state = 'superseded'\n"
        f"WHERE superseded_by IS NOT NULL\n"
        f"  AND belief_state = 'active'"
    ))

    # -----------------------------------------------------------------------
    # 4. Event-level idempotency: dedupe_key on event_log
    # -----------------------------------------------------------------------
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.event_log "
        f"ADD COLUMN IF NOT EXISTS dedupe_key TEXT"
    ))
    op.execute(sa.text(
        f"CREATE UNIQUE INDEX IF NOT EXISTS ix_mw_event_log_dedupe\n"
        f"ON {SCHEMA}.event_log(dedupe_key)\n"
        f"WHERE dedupe_key IS NOT NULL"
    ))

    # -----------------------------------------------------------------------
    # 5. Answer certificates table
    # -----------------------------------------------------------------------
    op.execute(sa.text(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.answer_certificates (
            id          BIGSERIAL PRIMARY KEY,
            query_text  TEXT NOT NULL,
            answer_text TEXT,
            confidence  FLOAT,
            stale_reason TEXT,
            stale_at    TIMESTAMPTZ,
            cleared_at  TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))

    # -----------------------------------------------------------------------
    # 6. Memory lineage junction table
    # -----------------------------------------------------------------------
    op.execute(sa.text(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.answer_certificate_memories (
            id             BIGSERIAL PRIMARY KEY,
            certificate_id BIGINT NOT NULL
                REFERENCES {SCHEMA}.answer_certificates(id) ON DELETE CASCADE,
            memory_id      BIGINT NOT NULL
                REFERENCES {SCHEMA}.memories(id) ON DELETE CASCADE,
            UNIQUE(certificate_id, memory_id)
        )
    """))

    # -----------------------------------------------------------------------
    # 7. Source lineage junction table
    # -----------------------------------------------------------------------
    op.execute(sa.text(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.answer_certificate_sources (
            id             BIGSERIAL PRIMARY KEY,
            certificate_id BIGINT NOT NULL
                REFERENCES {SCHEMA}.answer_certificates(id) ON DELETE CASCADE,
            source_id      BIGINT NOT NULL
                REFERENCES {SCHEMA}.sources(id) ON DELETE CASCADE,
            UNIQUE(certificate_id, source_id)
        )
    """))

    # -----------------------------------------------------------------------
    # 8. Indexes
    # -----------------------------------------------------------------------
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_answer_certs_created\n"
        f"ON {SCHEMA}.answer_certificates(created_at DESC)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_acm_cert\n"
        f"ON {SCHEMA}.answer_certificate_memories(certificate_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_acm_mem\n"
        f"ON {SCHEMA}.answer_certificate_memories(memory_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_acs_cert\n"
        f"ON {SCHEMA}.answer_certificate_sources(certificate_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_acs_src\n"
        f"ON {SCHEMA}.answer_certificate_sources(source_id)"
    ))


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_acs_src"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_acs_cert"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_acm_mem"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_acm_cert"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_answer_certs_created"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_event_log_dedupe"))
    op.execute(sa.text(f"DROP TABLE IF EXISTS {SCHEMA}.answer_certificate_sources"))
    op.execute(sa.text(f"DROP TABLE IF EXISTS {SCHEMA}.answer_certificate_memories"))
    op.execute(sa.text(f"DROP TABLE IF EXISTS {SCHEMA}.answer_certificates"))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.event_log DROP COLUMN IF EXISTS dedupe_key"
    ))
    for cname in (
        "chk_confidence_range", "chk_base_trust_range",
        "chk_required_roots_positive", "chk_source_class", "chk_belief_state",
    ):
        op.execute(sa.text(
            f"DO $body$ BEGIN\n"
            f"  IF EXISTS (\n"
            f"    SELECT 1 FROM pg_constraint\n"
            f"    WHERE conname = '{cname}'\n"
            f"      AND conrelid = '{SCHEMA}.memories'::regclass\n"
            f"  ) THEN\n"
            f"    ALTER TABLE {SCHEMA}.memories DROP CONSTRAINT {cname};\n"
            f"  END IF;\n"
            f"END $body$;"
        ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS base_trust"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS belief_state"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS source_class"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS required_roots"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS corrected_at"
    ))

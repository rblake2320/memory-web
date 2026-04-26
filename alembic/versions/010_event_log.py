"""Append-only event log with SHA-256 hash chain

Migration 010:
- event_log table: content-addressed append-only ledger of all memory mutations.
  Every significant state change (memory created, superseded, source invalidated,
  confidence changed) is logged here with a chained hash.

  The DB-level trigger prevents UPDATE and DELETE, making the log tamper-evident.
  A broken hash chain can be detected via GET /api/event_log/verify.

- Hash chain: hash = SHA-256(prev_hash || event_type || str(target_id) || payload_json)
  This makes it impossible to silently insert, reorder, or modify a past event.

Answers: "what did the system believe on date X?" for any date.

Revision ID: 010
Revises: 009
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None

SCHEMA = "memoryweb"


def upgrade() -> None:
    # Create the append-only event log table
    op.execute(sa.text(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.event_log (
            id          BIGSERIAL PRIMARY KEY,
            event_type  TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id   BIGINT NOT NULL,
            payload     JSONB NOT NULL,
            hash        TEXT NOT NULL,
            prev_hash   TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))

    op.execute(sa.text(f"""
        CREATE INDEX IF NOT EXISTS ix_{SCHEMA}_event_log_target
        ON {SCHEMA}.event_log (target_type, target_id, created_at)
    """))

    op.execute(sa.text(f"""
        CREATE INDEX IF NOT EXISTS ix_{SCHEMA}_event_log_hash
        ON {SCHEMA}.event_log (hash)
    """))

    op.execute(sa.text(f"""
        CREATE INDEX IF NOT EXISTS ix_{SCHEMA}_event_log_created
        ON {SCHEMA}.event_log (created_at)
    """))

    # Append-only enforcement: prevent UPDATE and DELETE on event_log
    op.execute(sa.text(f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.prevent_event_log_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'event_log is append-only: % not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """))

    op.execute(sa.text(f"""
        DROP TRIGGER IF EXISTS trg_event_log_no_update ON {SCHEMA}.event_log
    """))

    op.execute(sa.text(f"""
        CREATE TRIGGER trg_event_log_no_update
        BEFORE UPDATE OR DELETE ON {SCHEMA}.event_log
        FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.prevent_event_log_mutation()
    """))


def downgrade() -> None:
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_event_log_no_update ON {SCHEMA}.event_log"))
    op.execute(sa.text(f"DROP FUNCTION IF EXISTS {SCHEMA}.prevent_event_log_mutation()"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_{SCHEMA}_event_log_created"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_{SCHEMA}_event_log_hash"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_{SCHEMA}_event_log_target"))
    op.execute(sa.text(f"DROP TABLE IF EXISTS {SCHEMA}.event_log"))

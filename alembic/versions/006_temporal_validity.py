"""Add temporal validity columns to memories and attempts counter to pipeline_runs

Phase 5: Bi-temporal contradiction handling.
Phase 1c: Pipeline retry attempt counter.

memories:
  - valid_from TIMESTAMPTZ: when this fact became current (backfilled from created_at)
  - valid_until TIMESTAMPTZ: set when a newer fact supersedes this one (NULL = current)
  - superseded_by BIGINT FK: points to the memory that invalidated this one

pipeline_runs:
  - attempts INTEGER DEFAULT 0: watchdog retry counter (stops at MAX_PIPELINE_ATTEMPTS=5)

Revision ID: 006
Revises: 005
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

SCHEMA = "memoryweb"


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Temporal columns on memories
    # -----------------------------------------------------------------------
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.memories
        ADD COLUMN IF NOT EXISTS valid_from  TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS superseded_by BIGINT
            REFERENCES {SCHEMA}.memories(id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED
    """))

    # Backfill valid_from = created_at for all existing memories
    op.execute(sa.text(f"""
        UPDATE {SCHEMA}.memories
        SET valid_from = created_at
        WHERE valid_from IS NULL
    """))

    # Index for temporal queries (retrieval filters on valid_until IS NULL)
    op.execute(sa.text(f"""
        CREATE INDEX IF NOT EXISTS ix_{SCHEMA}_memories_temporal
        ON {SCHEMA}.memories (valid_from, valid_until)
        WHERE tombstoned_at IS NULL
    """))

    # -----------------------------------------------------------------------
    # 2. Attempt counter on pipeline_runs (Phase 1c)
    # -----------------------------------------------------------------------
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.pipeline_runs
        ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0
    """))


def downgrade() -> None:
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.pipeline_runs DROP COLUMN IF EXISTS attempts"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_{SCHEMA}_memories_temporal"))
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.memories
        DROP COLUMN IF EXISTS superseded_by,
        DROP COLUMN IF EXISTS valid_until,
        DROP COLUMN IF EXISTS valid_from
    """))

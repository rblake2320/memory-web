"""Add PostgreSQL full-text search tsvector columns to memories and segments

Phase 3: FTS hybrid retrieval.
- memories.fact_tsv: GENERATED ALWAYS AS to_tsvector('english', fact) STORED
- segments.summary_tsv: GENERATED ALWAYS AS to_tsvector('english', coalesce(summary,'')) STORED
- GIN indexes on both columns

After applying this migration, tier2_fts() in retrieval.py will be active.
Hybrid recall improvement: ~15-20% over trigram alone (catches stemmed matches).

Revision ID: 005
Revises: 004
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

SCHEMA = "memoryweb"


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Add tsvector column to memories (GENERATED ALWAYS AS STORED)
    #    No application code needed to maintain — PostgreSQL keeps it current.
    # -----------------------------------------------------------------------
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.memories
        ADD COLUMN IF NOT EXISTS fact_tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('english', fact)) STORED
    """))

    # GIN index for fast full-text search on memory facts
    op.execute(sa.text(f"""
        CREATE INDEX IF NOT EXISTS ix_{SCHEMA}_memories_fts
        ON {SCHEMA}.memories USING GIN (fact_tsv)
    """))

    # -----------------------------------------------------------------------
    # 2. Add tsvector column to segments
    # -----------------------------------------------------------------------
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.segments
        ADD COLUMN IF NOT EXISTS summary_tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('english', coalesce(summary, ''))) STORED
    """))

    # GIN index for fast full-text search on segment summaries
    op.execute(sa.text(f"""
        CREATE INDEX IF NOT EXISTS ix_{SCHEMA}_segments_fts
        ON {SCHEMA}.segments USING GIN (summary_tsv)
    """))


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_{SCHEMA}_segments_fts"))
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.segments DROP COLUMN IF EXISTS summary_tsv"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_{SCHEMA}_memories_fts"))
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS fact_tsv"))

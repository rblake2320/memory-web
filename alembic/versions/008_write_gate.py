"""Semantic write gate: canonical group tracking for near-duplicate detection

Migration 008:
- canonical_group_id BIGINT on memories — clusters memories that say the same thing.
  When a near-duplicate is detected post-embedding, the newer memory's canonical_group_id
  is set to point to the older (canonical) memory's id. The canonical memory's
  corroboration_count is incremented instead of creating a duplicate fact row.

The dedup logic runs in check_contradictions_batch (pipeline_tasks.py), triggered
by the embedding worker after a successful batch embed.

Thresholds:
  cos > 0.92 → near-duplicate → merge (increment corroboration_count on canonical)
  cos > 0.85 → similar → ask Ollama for contradiction / related classification

Revision ID: 008
Revises: 007
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

SCHEMA = "memoryweb"


def upgrade() -> None:
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.memories
        ADD COLUMN IF NOT EXISTS canonical_group_id BIGINT
    """))

    op.execute(sa.text(f"""
        CREATE INDEX IF NOT EXISTS ix_{SCHEMA}_memories_canonical_group
        ON {SCHEMA}.memories (canonical_group_id)
        WHERE canonical_group_id IS NOT NULL
    """))


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_{SCHEMA}_memories_canonical_group"))
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS canonical_group_id"))

"""Source trust tiers + cascade invalidation support

Migration 009:
- trust_tier SMALLINT on sources (mirrors derivation_tier on memories)
  1=user_explicit, 2=user_behavior, 3=llm_inference, 4=llm_synthesis, 5=external
  Used by retrieval to weight RRF scores: T1 facts rank higher than T4 facts.

- invalidated_at TIMESTAMPTZ on sources: set when a source is retroactively wrong.
  Cascade: all memories with source_id pointing here get confidence reduced by 50%
  and derivation_tier demoted. The memories remain visible but rank lower.
  Reversible via POST /api/sources/{id}/restore.

- invalidation_reason TEXT: why this source was invalidated.

Revision ID: 009
Revises: 008
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

SCHEMA = "memoryweb"


def upgrade() -> None:
    # Trust tier on sources
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.sources
        ADD COLUMN IF NOT EXISTS trust_tier SMALLINT NOT NULL DEFAULT 4
    """))

    # Invalidation columns
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.sources
        ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMPTZ
    """))

    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.sources
        ADD COLUMN IF NOT EXISTS invalidation_reason TEXT
    """))

    # Store pre-invalidation confidence on memories so /restore can recover it
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.memories
        ADD COLUMN IF NOT EXISTS pre_invalidation_confidence FLOAT
    """))


def downgrade() -> None:
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS pre_invalidation_confidence"))
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.sources DROP COLUMN IF EXISTS invalidation_reason"))
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.sources DROP COLUMN IF EXISTS invalidated_at"))
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.sources DROP COLUMN IF EXISTS trust_tier"))

"""Fix provenance race condition + add trust/derivation columns

Migration 007:
- source_id (direct FK) on memories for fast trust lookups — no 2-JOIN through provenance
- derivation_tier (how was the claim produced? 1=user_explicit … 5=external)
- ingested_at (transaction_time: when the system learned this, vs valid_from which is world-time)
- corroboration_count (how many independent sources agree on this claim)

The contradiction detection race condition (Phase 5 bug) is fixed in code:
memory_synthesizer.py no longer calls _check_and_handle_contradictions() synchronously.
Instead embedding_worker triggers check_contradictions_batch Celery task after embedding.

Revision ID: 007
Revises: 006
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

SCHEMA = "memoryweb"


def upgrade() -> None:
    # 1. Direct source_id on memories (denormalized for fast trust lookups)
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.memories
        ADD COLUMN IF NOT EXISTS source_id BIGINT
            REFERENCES {SCHEMA}.sources(id) ON DELETE SET NULL
    """))

    # Backfill source_id from provenance chain
    op.execute(sa.text(f"""
        UPDATE {SCHEMA}.memories m
        SET source_id = (
            SELECT mp.source_id
            FROM {SCHEMA}.memory_provenance mp
            WHERE mp.memory_id = m.id AND mp.source_id IS NOT NULL
            LIMIT 1
        )
        WHERE m.source_id IS NULL
    """))

    op.execute(sa.text(f"""
        CREATE INDEX IF NOT EXISTS ix_{SCHEMA}_memories_source_id
        ON {SCHEMA}.memories (source_id)
        WHERE source_id IS NOT NULL
    """))

    # 2. Derivation tier: how was this claim produced?
    # 1=user_explicit (conf ≤0.95), 2=user_behavior (≤0.85),
    # 3=llm_inference (≤0.75), 4=llm_synthesis (≤0.65), 5=external (≤0.55)
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.memories
        ADD COLUMN IF NOT EXISTS derivation_tier SMALLINT NOT NULL DEFAULT 4
    """))

    # 3. ingested_at = transaction_time (when system learned this, different from valid_from)
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.memories
        ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
    """))

    # Backfill: ingested_at = created_at for existing rows
    op.execute(sa.text(f"""
        UPDATE {SCHEMA}.memories
        SET ingested_at = created_at
        WHERE ingested_at > created_at OR ingested_at = now()
    """))

    # 4. Corroboration count: how many independent sources agree
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.memories
        ADD COLUMN IF NOT EXISTS corroboration_count INTEGER NOT NULL DEFAULT 1
    """))


def downgrade() -> None:
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS corroboration_count"))
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS ingested_at"))
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS derivation_tier"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_{SCHEMA}_memories_source_id"))
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS source_id"))

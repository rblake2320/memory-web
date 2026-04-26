"""Query expansion keywords stored at write time

Migration 011:
- search_keywords TEXT[] on memories: LLM-generated synonyms, abbreviations, and
  related concepts stored during synthesis. GIN index enables efficient array overlap
  queries (search_keywords @> ARRAY[...]).

  This is a write-time cost (one extra Ollama call per synthesis batch) but zero
  query-time cost — amortized across all future searches. Catches queries that
  don't match the literal fact text but would match a synonym.

  Example: fact "PostgreSQL listens on port 5432" gets keywords
  ["postgres", "pg", "database", "port", "5432", "sql", "db"].
  A query for "pg port" finds it via the keyword tier even though "pg" ≠ "PostgreSQL".

Revision ID: 011
Revises: 010
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None

SCHEMA = "memoryweb"


def upgrade() -> None:
    op.execute(sa.text(f"""
        ALTER TABLE {SCHEMA}.memories
        ADD COLUMN IF NOT EXISTS search_keywords TEXT[] DEFAULT '{{}}'
    """))

    op.execute(sa.text(f"""
        CREATE INDEX IF NOT EXISTS ix_{SCHEMA}_memories_keywords
        ON {SCHEMA}.memories USING GIN (search_keywords)
    """))


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_{SCHEMA}_memories_keywords"))
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS search_keywords"))

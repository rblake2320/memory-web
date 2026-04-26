"""Users table for JWT authentication.

Migration 013b:
Creates memoryweb.users table for per-tenant user accounts.
Depends on 013a (tenants table must exist).

Revision ID: 013b
Revises: 013a
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa

revision = "013b"
down_revision = "013a"
branch_labels = None
depends_on = None

SCHEMA = "memoryweb"


def upgrade() -> None:
    op.execute(sa.text(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.users (
            id          BIGSERIAL PRIMARY KEY,
            tenant_id   UUID NOT NULL
                REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE,
            email       VARCHAR(255) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role        VARCHAR(30) NOT NULL DEFAULT 'user',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON {SCHEMA}.users(email)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_users_tenant ON {SCHEMA}.users(tenant_id)"
    ))


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_users_tenant"))
    op.execute(sa.text(f"DROP INDEX IF EXISTS {SCHEMA}.uq_users_email"))
    op.execute(sa.text(f"DROP TABLE IF EXISTS {SCHEMA}.users"))

"""Multi-Tenant Schema

Migration 013a:
Adds first-class multi-tenancy to MemoryWeb.  All existing data is assigned
to the default tenant so the upgrade is fully non-destructive.

A. memoryweb.tenants — tenant registry with slug uniqueness
B. memoryweb.tenant_api_keys — hashed API keys per tenant (NOT a column on tenants)
C. tenant_id column on all 17 data tables — FK → tenants, NOT NULL, DEFAULT default-tenant
D. Tenant-scoped unique indexes to replace 3 global unique constraints
   (uq_memories_fact_hash, uq_sources_hash, uq_embeddings_target)
E. transaction_time on memories — bitemporal "system time" axis, backfilled from ingested_at
F. derivation_tier CHECK constraint (1–5) + column comment

Implementation notes:
- All DDL via op.execute(sa.text(...)) — consistent with migrations 009-012.
- DO-block guards used for all constraints (PostgreSQL lacks ADD CONSTRAINT IF NOT EXISTS).
- tenant_id columns are NOT dropped in downgrade() — dropping a populated column would
  silently destroy tenant data.  downgrade() removes only indexes/tables/new constraints
  and leaves tenant_id columns in place; manual DDL is required to complete a full rollback.
- The three global unique constraints are dropped before the tenant-scoped indexes are
  created so that we never have duplicate-enforcement gaps for the default tenant.

Revision ID: 013a
Revises: 012
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa

revision = "013a"
down_revision = "012"
branch_labels = None
depends_on = None

SCHEMA = "memoryweb"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Ordered list of the 17 tables that receive tenant_id.
# Order matters: junction tables that reference others come after their parents.
_TENANT_TABLES = [
    "sources",
    "conversations",
    "messages",
    "segments",
    "tags",
    "entity_mentions",
    "memories",
    "memory_provenance",
    "memory_links",
    "embeddings",
    "retention_log",
    "pipeline_runs",
    "embedding_queue",
    "event_log",
    "answer_certificates",
    "answer_certificate_memories",
    "answer_certificate_sources",
]


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Create memoryweb.tenants
    # -----------------------------------------------------------------------
    op.execute(sa.text(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.tenants (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name        VARCHAR(255) NOT NULL,
            slug        VARCHAR(100) NOT NULL,
            email       VARCHAR(255),
            plan        VARCHAR(30)  NOT NULL DEFAULT 'community',
            disabled_at TIMESTAMPTZ,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """))

    op.execute(sa.text(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_slug "
        f"ON {SCHEMA}.tenants(slug)"
    ))

    # Seed the default tenant so all FK backfills succeed
    op.execute(sa.text(
        f"INSERT INTO {SCHEMA}.tenants (id, name, slug, plan)\n"
        f"VALUES ('{DEFAULT_TENANT_ID}', 'default', 'default', 'community')\n"
        f"ON CONFLICT (id) DO NOTHING"
    ))

    # -----------------------------------------------------------------------
    # 2. Create memoryweb.tenant_api_keys
    # -----------------------------------------------------------------------
    op.execute(sa.text(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.tenant_api_keys (
            id           BIGSERIAL PRIMARY KEY,
            tenant_id    UUID         NOT NULL
                             REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE,
            key_prefix   VARCHAR(12)  NOT NULL,
            key_hash     VARCHAR(128) NOT NULL,
            name         VARCHAR(255),
            scopes       TEXT[]       NOT NULL DEFAULT '{{}}',
            last_used_at TIMESTAMPTZ,
            revoked_at   TIMESTAMPTZ,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """))

    op.execute(sa.text(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_api_keys_prefix "
        f"ON {SCHEMA}.tenant_api_keys(key_prefix)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_tenant_api_keys_tenant "
        f"ON {SCHEMA}.tenant_api_keys(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_tenant_api_keys_hash "
        f"ON {SCHEMA}.tenant_api_keys(key_hash)"
    ))

    # -----------------------------------------------------------------------
    # 3. Add tenant_id to each of the 17 data tables
    #    Pattern per table:
    #      a. ADD COLUMN IF NOT EXISTS (nullable so backfill can run)
    #      b. Backfill NULL rows with the default tenant UUID
    #      c. SET NOT NULL
    #      d. SET DEFAULT so new inserts inherit the default tenant
    # -----------------------------------------------------------------------

    # -- sources
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.sources "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.sources "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.sources ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.sources "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- conversations
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.conversations "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.conversations "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.conversations ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.conversations "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- messages
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.messages "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.messages "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.messages ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.messages "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- segments
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.segments "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.segments "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.segments ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.segments "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- tags
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.tags "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.tags "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.tags ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.tags "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- entity_mentions
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.entity_mentions "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.entity_mentions "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.entity_mentions ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.entity_mentions "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- memories
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.memories "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- memory_provenance
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memory_provenance "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.memory_provenance "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memory_provenance ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memory_provenance "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- memory_links
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memory_links "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.memory_links "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memory_links ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memory_links "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- embeddings
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.embeddings "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.embeddings "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.embeddings ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.embeddings "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- retention_log
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.retention_log "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.retention_log "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.retention_log ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.retention_log "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- pipeline_runs
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.pipeline_runs "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.pipeline_runs "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.pipeline_runs ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.pipeline_runs "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- embedding_queue
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.embedding_queue "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.embedding_queue "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.embedding_queue ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.embedding_queue "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- event_log  (append-only trigger must be disabled for backfill)
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.event_log "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    # Temporarily disable the append-only trigger so we can backfill tenant_id
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.event_log DISABLE TRIGGER trg_event_log_no_update"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.event_log "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.event_log ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.event_log "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))
    # Re-enable the append-only trigger
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.event_log ENABLE TRIGGER trg_event_log_no_update"
    ))

    # -- answer_certificates
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.answer_certificates "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.answer_certificates "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.answer_certificates ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.answer_certificates "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- answer_certificate_memories
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.answer_certificate_memories "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.answer_certificate_memories "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.answer_certificate_memories ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.answer_certificate_memories "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -- answer_certificate_sources
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.answer_certificate_sources "
        f"ADD COLUMN IF NOT EXISTS tenant_id UUID "
        f"REFERENCES {SCHEMA}.tenants(id) ON DELETE CASCADE"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.answer_certificate_sources "
        f"SET tenant_id = '{DEFAULT_TENANT_ID}'::uuid "
        f"WHERE tenant_id IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.answer_certificate_sources ALTER COLUMN tenant_id SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.answer_certificate_sources "
        f"ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}'::uuid"
    ))

    # -----------------------------------------------------------------------
    # 4. Tenant-scoped indexes on all 17 tables
    # -----------------------------------------------------------------------
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_sources_tenant "
        f"ON {SCHEMA}.sources(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_conversations_tenant "
        f"ON {SCHEMA}.conversations(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_messages_tenant "
        f"ON {SCHEMA}.messages(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_segments_tenant "
        f"ON {SCHEMA}.segments(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_tags_tenant "
        f"ON {SCHEMA}.tags(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_entity_mentions_tenant "
        f"ON {SCHEMA}.entity_mentions(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_memories_tenant "
        f"ON {SCHEMA}.memories(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_memory_provenance_tenant "
        f"ON {SCHEMA}.memory_provenance(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_memory_links_tenant "
        f"ON {SCHEMA}.memory_links(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_embeddings_tenant "
        f"ON {SCHEMA}.embeddings(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_retention_log_tenant "
        f"ON {SCHEMA}.retention_log(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_pipeline_runs_tenant "
        f"ON {SCHEMA}.pipeline_runs(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_embedding_queue_tenant "
        f"ON {SCHEMA}.embedding_queue(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_event_log_tenant "
        f"ON {SCHEMA}.event_log(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_answer_certificates_tenant "
        f"ON {SCHEMA}.answer_certificates(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_answer_certificate_memories_tenant "
        f"ON {SCHEMA}.answer_certificate_memories(tenant_id)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_answer_certificate_sources_tenant "
        f"ON {SCHEMA}.answer_certificate_sources(tenant_id)"
    ))

    # NOTE: Composite indexes on memories using transaction_time are created
    # AFTER the transaction_time column is added below (Part 5).

    # -----------------------------------------------------------------------
    # 5. Add transaction_time to memories (bitemporal system-time axis)
    #    Add nullable first so that backfill can write ingested_at/created_at
    #    values before we enforce NOT NULL — avoids DEFAULT now() masking real
    #    ingestion timestamps for every existing row.
    # -----------------------------------------------------------------------
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories "
        f"ADD COLUMN IF NOT EXISTS transaction_time TIMESTAMPTZ"
    ))
    op.execute(sa.text(
        f"UPDATE {SCHEMA}.memories "
        f"SET transaction_time = COALESCE(ingested_at, created_at, now()) "
        f"WHERE transaction_time IS NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories ALTER COLUMN transaction_time SET NOT NULL"
    ))
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories ALTER COLUMN transaction_time SET DEFAULT now()"
    ))
    # Composite bitemporal + tenant indexes (now safe — transaction_time exists)
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_memories_tenant_tx "
        f"ON {SCHEMA}.memories(tenant_id, transaction_time DESC)"
    ))
    op.execute(sa.text(
        f"CREATE INDEX IF NOT EXISTS ix_mw_memories_tenant_valid "
        f"ON {SCHEMA}.memories(tenant_id, valid_from, valid_until)"
    ))

    # -----------------------------------------------------------------------
    # 6. derivation_tier CHECK constraint and column comment
    # -----------------------------------------------------------------------
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF NOT EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'chk_derivation_tier_range'\n"
        f"      AND conrelid = '{SCHEMA}.memories'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.memories ADD CONSTRAINT chk_derivation_tier_range\n"
        f"    CHECK (derivation_tier BETWEEN 1 AND 5);\n"
        f"  END IF;\n"
        f"END $body$;"
    ))
    op.execute(sa.text(
        f"COMMENT ON COLUMN {SCHEMA}.memories.derivation_tier IS "
        f"'1=USER_EXPLICIT, 2=USER_BEHAVIOR, 3=LLM_INFERENCE, 4=LLM_SYNTHESIS, 5=EXTERNAL'"
    ))

    # -----------------------------------------------------------------------
    # 7. Tenant-scoped unique indexes — drop global constraints first, then
    #    create per-tenant replacements.  Dropping first ensures there is no
    #    window where both constraints apply simultaneously.
    # -----------------------------------------------------------------------

    # memories.fact_hash → tenant-scoped
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'uq_memories_fact_hash'\n"
        f"      AND conrelid = '{SCHEMA}.memories'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.memories DROP CONSTRAINT uq_memories_fact_hash;\n"
        f"  END IF;\n"
        f"END $body$;"
    ))
    op.execute(sa.text(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_tenant_fact_hash "
        f"ON {SCHEMA}.memories(tenant_id, fact_hash)"
    ))

    # sources.source_hash → tenant-scoped
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'uq_sources_hash'\n"
        f"      AND conrelid = '{SCHEMA}.sources'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.sources DROP CONSTRAINT uq_sources_hash;\n"
        f"  END IF;\n"
        f"END $body$;"
    ))
    op.execute(sa.text(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_sources_tenant_hash "
        f"ON {SCHEMA}.sources(tenant_id, source_hash)"
    ))

    # embeddings target → tenant-scoped
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'uq_embeddings_target'\n"
        f"      AND conrelid = '{SCHEMA}.embeddings'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.embeddings DROP CONSTRAINT uq_embeddings_target;\n"
        f"  END IF;\n"
        f"END $body$;"
    ))
    op.execute(sa.text(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_embeddings_tenant_target "
        f"ON {SCHEMA}.embeddings(tenant_id, target_type, target_id)"
    ))


def downgrade() -> None:
    # -----------------------------------------------------------------------
    # NOTE: tenant_id columns are intentionally NOT dropped here.
    # Removing a populated column would silently destroy multi-tenant data.
    # To fully revert: manually run
    #   ALTER TABLE memoryweb.<table> DROP COLUMN tenant_id;
    # for each of the 17 tables after confirming data is safe.
    # -----------------------------------------------------------------------

    # 7. Restore global unique constraints, drop tenant-scoped indexes
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.uq_embeddings_tenant_target"
    ))
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF NOT EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'uq_embeddings_target'\n"
        f"      AND conrelid = '{SCHEMA}.embeddings'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.embeddings ADD CONSTRAINT uq_embeddings_target\n"
        f"    UNIQUE (target_type, target_id);\n"
        f"  END IF;\n"
        f"END $body$;"
    ))

    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.uq_sources_tenant_hash"
    ))
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF NOT EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'uq_sources_hash'\n"
        f"      AND conrelid = '{SCHEMA}.sources'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.sources ADD CONSTRAINT uq_sources_hash\n"
        f"    UNIQUE (source_hash);\n"
        f"  END IF;\n"
        f"END $body$;"
    ))

    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.uq_memories_tenant_fact_hash"
    ))
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF NOT EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'uq_memories_fact_hash'\n"
        f"      AND conrelid = '{SCHEMA}.memories'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.memories ADD CONSTRAINT uq_memories_fact_hash\n"
        f"    UNIQUE (fact_hash);\n"
        f"  END IF;\n"
        f"END $body$;"
    ))

    # 6. Drop derivation_tier constraint and comment
    op.execute(sa.text(
        f"DO $body$ BEGIN\n"
        f"  IF EXISTS (\n"
        f"    SELECT 1 FROM pg_constraint\n"
        f"    WHERE conname = 'chk_derivation_tier_range'\n"
        f"      AND conrelid = '{SCHEMA}.memories'::regclass\n"
        f"  ) THEN\n"
        f"    ALTER TABLE {SCHEMA}.memories DROP CONSTRAINT chk_derivation_tier_range;\n"
        f"  END IF;\n"
        f"END $body$;"
    ))
    op.execute(sa.text(
        f"COMMENT ON COLUMN {SCHEMA}.memories.derivation_tier IS NULL"
    ))

    # 5. Drop transaction_time from memories
    op.execute(sa.text(
        f"ALTER TABLE {SCHEMA}.memories DROP COLUMN IF EXISTS transaction_time"
    ))

    # 4. Drop all tenant-scoped indexes (composite memories indexes first)
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_memories_tenant_valid"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_memories_tenant_tx"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_answer_certificate_sources_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_answer_certificate_memories_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_answer_certificates_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_event_log_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_embedding_queue_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_pipeline_runs_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_retention_log_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_embeddings_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_memory_links_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_memory_provenance_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_memories_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_entity_mentions_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_tags_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_segments_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_messages_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_conversations_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_mw_sources_tenant"
    ))

    # 2. Drop tenant_api_keys table and its indexes
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_tenant_api_keys_hash"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.ix_tenant_api_keys_tenant"
    ))
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.uq_tenant_api_keys_prefix"
    ))
    op.execute(sa.text(
        f"DROP TABLE IF EXISTS {SCHEMA}.tenant_api_keys"
    ))

    # 1. Drop tenants table and its index
    #    WARNING: This will fail with FK violations if any tenant_id columns
    #    still reference this table.  Remove tenant_id columns from all 17
    #    tables manually before running this step.
    op.execute(sa.text(
        f"DROP INDEX IF EXISTS {SCHEMA}.uq_tenants_slug"
    ))
    op.execute(sa.text(
        f"DROP TABLE IF EXISTS {SCHEMA}.tenants"
    ))

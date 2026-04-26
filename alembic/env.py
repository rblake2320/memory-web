import os
import sys
import warnings
from logging.config import fileConfig

from sqlalchemy import create_engine, pool, text
from alembic import context

# Make app importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.config import settings
from app.models import Base   # import all models so metadata is populated

config = context.config

# Do NOT set sqlalchemy.url via configparser — % in URL password causes errors.
# We create the engine directly in run_migrations_online().

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
SCHEMA = settings.MW_DB_SCHEMA


def _setup_extensions(connection):
    """Create schema and extensions, tolerating missing pgvector."""
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    connection.commit()

    # pgvector — optional
    try:
        with connection.begin_nested():
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as e:
        warnings.warn(
            f"pgvector not installed ({e}). "
            "Tier-3 vector search will be unavailable. "
            "See PGVECTOR_INSTALL.md for installation steps.",
            UserWarning,
            stacklevel=2,
        )


def run_migrations_offline() -> None:
    context.configure(
        url=settings.MW_DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(settings.MW_DATABASE_URL, poolclass=pool.NullPool)

    # Run schema/extension setup on its own connection so its commit does not
    # interfere with Alembic's transactional DDL lifecycle.
    with connectable.connect() as setup_conn:
        _setup_extensions(setup_conn)

    # Open a fresh connection for migrations (no prior commit on this connection).
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

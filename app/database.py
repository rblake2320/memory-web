import re
import threading
from contextlib import contextmanager
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import settings

# Validate schema name before any SQL is built (prevent injection via config)
_SCHEMA_RE = re.compile(r'^[a-z_][a-z0-9_]*$')
if not _SCHEMA_RE.match(settings.MW_DB_SCHEMA):
    raise ValueError(
        f"MW_DB_SCHEMA '{settings.MW_DB_SCHEMA}' is invalid. "
        "Use only lowercase letters, digits, and underscores."
    )

engine = create_engine(
    settings.MW_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

SCHEMA = settings.MW_DB_SCHEMA


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Tenant context (thread-local)
# ---------------------------------------------------------------------------

_tenant_local = threading.local()

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def set_tenant_context(tenant_id: str) -> None:
    """Set the current tenant for this thread."""
    _tenant_local.tenant_id = str(tenant_id)


def get_tenant_context() -> str:
    """Get the current tenant for this thread. Returns default if not set."""
    return getattr(_tenant_local, "tenant_id", DEFAULT_TENANT_ID)


def clear_tenant_context() -> None:
    """Clear tenant context (use in tests or after request)."""
    if hasattr(_tenant_local, "tenant_id"):
        del _tenant_local.tenant_id


# ---------------------------------------------------------------------------
# Schema / extension setup
# ---------------------------------------------------------------------------

def ensure_schema_and_extensions() -> None:
    """Create schema, pgvector extension, and pg_trgm extension if missing."""
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "pgvector not available (vector search disabled): %s. "
                "See PGVECTOR_INSTALL.md for installation instructions.", e
            )
            conn.rollback()
            # Reconnect for pg_trgm
            with engine.connect() as conn2:
                conn2.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
                conn2.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
                conn2.commit()
            return
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.commit()


# ---------------------------------------------------------------------------
# Session / connection factories
# ---------------------------------------------------------------------------

def get_db():
    """FastAPI dependency: yields a DB session with tenant context set."""
    db = SessionLocal()
    try:
        tenant_id = get_tenant_context()
        # Set PostgreSQL session variable for RLS (013d will enable this)
        db.execute(text(f"SET LOCAL app.current_tenant = '{tenant_id}'"))
        yield db
    finally:
        db.close()


@contextmanager
def db_session(tenant_id: str | None = None):
    """Context manager for non-FastAPI code (tasks, scripts)."""
    db = SessionLocal()
    try:
        effective_tenant = tenant_id or get_tenant_context()
        db.execute(text(f"SET LOCAL app.current_tenant = '{effective_tenant}'"))
        set_tenant_context(effective_tenant)
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        if tenant_id:  # only clear if we explicitly set it
            clear_tenant_context()


@contextmanager
def tenant_connection(tenant_id: str | None = None):
    """Context manager for raw SQL paths that need tenant isolation.

    Usage:
        with tenant_connection(tenant_id) as conn:
            conn.execute(text("SELECT ..."))
    """
    effective_tenant = tenant_id or get_tenant_context()
    with engine.connect() as conn:
        conn.execute(text(f"SET LOCAL app.current_tenant = '{effective_tenant}'"))
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

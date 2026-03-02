import re
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


def get_db():
    """FastAPI dependency: yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session():
    """Context manager for non-FastAPI code (tasks, scripts)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

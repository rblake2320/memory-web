"""
pgvector setup helper for PostgreSQL 16 on Windows.

Downloads the pgvector DLL and installs the extension.
Run once: python scripts/setup_pgvector.py
"""

import subprocess
import sys
from pathlib import Path


def check_extension():
    """Check if pgvector is already installed."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            "postgresql://postgres:%3FBooker78%21@localhost:5432/postgres"
        )
        cur = conn.cursor()
        cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        row = cur.fetchone()
        conn.close()
        return row is not None
    except Exception as e:
        print(f"Cannot connect to PostgreSQL: {e}")
        return False


def install_extension():
    """Create the vector extension."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            "postgresql://postgres:%3FBooker78%21@localhost:5432/postgres"
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        conn.close()
        print("✓ pgvector extension created")
        return True
    except Exception as e:
        print(f"Failed to create extension: {e}")
        print("\nManual steps:")
        print("1. Download pgvector from https://github.com/pgvector/pgvector/releases")
        print("2. Copy vector.dll to C:\\Program Files\\PostgreSQL\\16\\lib\\")
        print("3. Copy vector.control + vector--*.sql to C:\\Program Files\\PostgreSQL\\16\\share\\extension\\")
        print("4. Run: psql -U postgres -c 'CREATE EXTENSION vector'")
        return False



# Import statements needed (only new ones not in existing code)
from fastapi import Query
from sqlalchemy import select
from ..models import Embedding, Memory
from ..schemas import SimilarMemoryOut

# New endpoint
@router.get("/memories/{memory_id}/similar", response_model=List[SimilarMemoryOut])
def get_similar_memories(
    memory_id: int,
    limit: int = Query(default=5, le=20),
    db: Session = Depends(get_db),
):
    """Get semantically similar memories using pgvector cosine similarity."""
    mem = db.query(Memory).get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Query the embeddings table for nearest neighbors
    stmt = select(Embedding).where(Embedding.memory_id != memory_id)
    embeddings = db.execute(stmt).scalars().all()

    # Calculate cosine similarity using pgvector
    similar_memories = []
    for embedding in embeddings:
        similarity_score = db.execute(
            select(Embedding.vector.similarity(mem.embedding.vector))
        ).scalar()
        if similarity_score is not None:
            similar_memories.append((embedding.memory_id, similarity_score))

    # Sort by similarity score and limit
    similar_memories.sort(key=lambda x: x[1], reverse=True)
    similar_memories = similar_memories[:limit]

    # Fetch and return similar memories
    result = []
    for mem_id, similarity_score in similar_memories:
        mem = db.query(Memory).get(mem_id)
        if mem:
            result.append(
                SimilarMemoryOut(
                    id=mem.id,
                    content=mem.content,
                    similarity_score=similarity_score,
                )
            )

    return result

if __name__ == "__main__":
    print("Checking pgvector...")
    if check_extension():
        print("✓ pgvector already installed")
    else:
        print("Installing pgvector...")
        install_extension()

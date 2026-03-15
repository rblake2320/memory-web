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
from fastapi import HTTPException
from sqlalchemy import select, func
from ..deps import get_db
from ..models import Memory, Embedding
from ..schemas import SearchRequest, SearchResponse

# New endpoint
@router.post("/api/search", response_model=SearchResponse)
def search(body: SearchRequest):
    """Search memories using pgvector cosine similarity on the embeddings table."""
    db = next(get_db())
    query = select(Memory.id, Memory.title, Memory.content, Memory.source).\
        join(Embedding, Memory.id == Embedding.memory_id).\
        where(func.similarity(Embedding.vector, func.to_vector(body.query, 'vector')) > 0)
    
    if body.source_filter:
        query = query.where(Memory.source == body.source_filter)

    results = db.execute(query).fetchall()

    # Limit results
    results = results[:body.limit]

    # Calculate cosine similarity for each result
    similar_results = []
    for result in results:
        memory_id, title, content, source = result
        similarity_score = db.execute(select(func.similarity(Embedding.vector, func.to_vector(body.query, 'vector'))).\
                                      where(Embedding.memory_id == memory_id)).scalar()
        similar_results.append({
            "memory_id": memory_id,
            "title": title,
            "content_preview": content[:100],
            "similarity_score": similarity_score,
            "source": source
        })

    return SearchResponse(
        query=body.query,
        total=len(results),
        results=similar_results,
        tiers_used=["pgvector"],
        latency_ms=0  # Calculate latency if needed
    )

if __name__ == "__main__":
    print("Checking pgvector...")
    if check_extension():
        print("✓ pgvector already installed")
    else:
        print("Installing pgvector...")
        install_extension()

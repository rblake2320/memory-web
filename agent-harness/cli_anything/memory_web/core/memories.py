"""Memory CRUD operations — thin wrappers over the HTTP client."""

from typing import Any, Dict, List, Optional

from .client import MemoryWebClient


def list_memories(client: MemoryWebClient, page: int = 1, page_size: int = 50,
                  category: Optional[str] = None,
                  min_importance: Optional[int] = None,
                  include_tombstoned: bool = False) -> Dict:
    """Return a paginated list of memories."""
    return client.list_memories(
        page=page,
        page_size=page_size,
        category=category,
        min_importance=min_importance,
        include_tombstoned=include_tombstoned,
    )


def get_memory(client: MemoryWebClient, memory_id: int) -> Dict:
    """Fetch a single memory by ID (increments access count)."""
    return client.get_memory(memory_id)


def get_provenance(client: MemoryWebClient, memory_id: int) -> Any:
    """Get full provenance chain for a memory."""
    return client.get_memory_provenance(memory_id)


def get_history(client: MemoryWebClient, memory_id: int) -> Dict:
    """Get the event history for a memory (lifecycle events)."""
    return client.get_memory_history(memory_id)


def mark_helpful(client: MemoryWebClient, memory_id: int) -> Dict:
    """Signal that a memory was useful; bumps utility score."""
    return client.mark_helpful(memory_id)


def delete_memory(client: MemoryWebClient, memory_id: int) -> Dict:
    """Soft-delete (tombstone) a memory. Reversible via retain/restore."""
    return client.delete_memory(memory_id)


def list_conversations(client: MemoryWebClient, source_id: Optional[int] = None,
                       limit: int = 50, offset: int = 0) -> Any:
    """List ingested conversations."""
    return client.list_conversations(source_id=source_id, limit=limit, offset=offset)


def get_conversation_segments(client: MemoryWebClient, conversation_id: int) -> Any:
    """Get all segments for a conversation."""
    return client.get_conversation_segments(conversation_id)


def list_sources(client: MemoryWebClient) -> Any:
    """List all ingested data sources."""
    return client.list_sources()


def delete_source(client: MemoryWebClient, source_id: int, hard: bool = False) -> Dict:
    """Delete a source and cascade to derived records.

    Args:
        hard: If True, immediately purge. If False (default), tombstone.
    """
    return client.delete_source(source_id, hard=hard)


def invalidate_source(client: MemoryWebClient, source_id: int, reason: str = "") -> Dict:
    """Mark a source retroactively wrong; cascades confidence demotion."""
    return client.invalidate_source(source_id, reason=reason)


def restore_source(client: MemoryWebClient, source_id: int) -> Dict:
    """Restore a previously invalidated source."""
    return client.restore_source(source_id)


def list_certificates(client: MemoryWebClient, limit: int = 50,
                      stale_only: bool = False) -> Dict:
    """List answer certificates."""
    return client.list_certificates(limit=limit, stale_only=stale_only)


def verify_event_log(client: MemoryWebClient) -> Dict:
    """Verify the append-only event log hash chain integrity."""
    return client.verify_event_log()

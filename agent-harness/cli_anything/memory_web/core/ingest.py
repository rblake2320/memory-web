"""Ingest operations for the MemoryWeb CLI."""

import time
from typing import Any, Dict, Optional

from .client import MemoryWebClient


def ingest_session(client: MemoryWebClient, path: str, force: bool = False) -> Dict:
    """Ingest one Claude session JSONL file asynchronously.

    Args:
        path:  Absolute path to the .jsonl session file on the server.
        force: Re-ingest even if the file hash has already been processed.

    Returns:
        TaskResponse with task_id to poll with ingest_status().
    """
    return client.ingest_session(path=path, force=force)


def ingest_all_sessions(client: MemoryWebClient, directory: Optional[str] = None,
                        force: bool = False) -> Dict:
    """Ingest all .jsonl session files from a directory.

    Args:
        directory: Override the server's default sessions directory.
        force:     Re-ingest already-processed files.
    """
    return client.ingest_all_sessions(directory=directory, force=force)


def ingest_shared_chat(client: MemoryWebClient, directory: Optional[str] = None,
                       limit: Optional[int] = None, force: bool = False) -> Dict:
    """Ingest AI Army shared chat markdown files.

    Args:
        directory: Override shared-chat directory.
        limit:     Max number of files to process.
        force:     Re-ingest already-processed files.
    """
    return client.ingest_shared_chat(directory=directory, limit=limit, force=force)


def ingest_sqlite(client: MemoryWebClient, path: Optional[str] = None) -> Dict:
    """Import memories from an existing SQLite memory.db file.

    Args:
        path: Override the server's default memory.db path.
    """
    return client.ingest_sqlite(path=path)


def ingest_sample(client: MemoryWebClient) -> Dict:
    """Load built-in sample conversations for first-time exploration.

    Safe to call multiple times — idempotent via content hash.
    """
    return client.ingest_sample()


def poll_task(client: MemoryWebClient, task_id: str,
              timeout: int = 120, poll_interval: float = 2.0) -> Dict:
    """Poll an ingest task until it completes or times out.

    Args:
        task_id:       Task ID returned by an ingest operation.
        timeout:       Max seconds to wait.
        poll_interval: Seconds between polls.

    Returns:
        Final IngestStatusResponse dict.

    Raises:
        TimeoutError: Task did not finish within the timeout period.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.ingest_status(task_id)
        state = status.get("status", "")
        if state in ("SUCCESS", "FAILURE", "REVOKED"):
            return status
        time.sleep(poll_interval)
    raise TimeoutError(
        f"Task {task_id} did not complete within {timeout}s. "
        f"Last status: {client.ingest_status(task_id)}"
    )


def ingest_status(client: MemoryWebClient, task_id: str) -> Dict:
    """Get the current status of an ingest task.

    Args:
        task_id: Task ID from a prior ingest call.
    """
    return client.ingest_status(task_id)


def run_pipeline(client: MemoryWebClient, source_id: int) -> Dict:
    """Manually trigger the full processing pipeline for a source.

    This re-runs segmentation → extraction → embedding for all conversations
    belonging to the given source.

    Args:
        source_id: ID of the source to reprocess.
    """
    return client.run_pipeline(source_id)

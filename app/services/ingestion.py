"""
Orchestrate ingestion: parse source files → insert conversations + messages → DB.
Returns (source_id, stats_dict).
"""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from ..config import settings
from ..database import db_session
from ..models import Conversation, Message, Source
from ..parsers.claude_session import ParsedConversation, parse_session_file, iter_session_files
from ..parsers.claude_history import parse_history_file
from ..parsers.chatgpt_export import parse_chatgpt_export
from ..parsers.shared_chat import SharedChatMessage, batch_parse_shared_chat, parse_shared_chat_file
from ..parsers.sqlite_memory import import_journal_entries, import_knowledge_items, list_tables

logger = logging.getLogger(__name__)


def _file_hash(path: str, chunk: int = 1 << 20) -> str:
    """SHA-256 hash of first 1MB (fast dedup check)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                h.update(block)
    except OSError:
        pass
    return h.hexdigest()


def _get_or_create_source(
    db: Session,
    source_type: str,
    source_path: str,
    source_hash: str,
    force: bool = False,
) -> Tuple[Optional[Source], bool]:
    """
    Returns (source, is_new).
    If hash matches and not force → returns existing source, is_new=False.
    """
    existing = db.query(Source).filter(Source.source_hash == source_hash).first()
    if existing and not force:
        return existing, False

    p = Path(source_path)
    size = p.stat().st_size if p.exists() else None

    if existing and force:
        # Update existing
        existing.ingested_at = datetime.utcnow()
        db.flush()
        return existing, True

    src = Source(
        source_type=source_type,
        source_path=source_path,
        source_hash=source_hash,
        file_size_bytes=size,
    )
    db.add(src)
    db.flush()
    return src, True


def ingest_session_file(path: str, force: bool = False) -> Dict[str, Any]:
    """
    Ingest a single Claude session JSONL file.
    Returns stats: {source_id, conversations, messages, skipped}.
    """
    path = str(Path(path).resolve())
    source_hash = _file_hash(path)

    with db_session() as db:
        source, is_new = _get_or_create_source(
            db, "claude_session", path, source_hash, force
        )
        if not is_new:
            return {"source_id": source.id, "skipped": True, "reason": "hash_unchanged"}

        parsed: ParsedConversation = parse_session_file(path)

        # Create one conversation per session file
        conv = Conversation(
            source_id=source.id,
            external_id=parsed.external_id,
            title=f"Session {parsed.external_id[:16]}",
            participant="claude",
            started_at=parsed.started_at,
            ended_at=parsed.ended_at,
            message_count=len(parsed.messages),
        )
        db.add(conv)
        db.flush()

        msg_objs = []
        for i, pm in enumerate(parsed.messages):
            msg = Message(
                conversation_id=conv.id,
                ordinal=i,
                role=pm.role,
                content=pm.content.replace('\x00', '')[:32000] if pm.content else None,  # strip NUL + cap at 32KB
                raw_json=pm.raw_json,
                external_uuid=pm.external_uuid or None,
                char_offset_start=pm.char_offset_start,
                char_offset_end=pm.char_offset_end,
                sent_at=pm.sent_at,
                token_count=pm.token_count,
            )
            msg_objs.append(msg)

        db.bulk_save_objects(msg_objs)

        source.message_count = len(parsed.messages)
        db.flush()

        return {
            "source_id": source.id,
            "conversations": 1,
            "messages": len(parsed.messages),
            "skipped": False,
        }


def ingest_all_sessions(
    directory: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Ingest all .jsonl files in the sessions directory."""
    directory = directory or settings.MW_SESSIONS_DIR
    total_conv = total_msg = 0
    sources = []

    for path in iter_session_files(directory):
        try:
            stats = ingest_session_file(str(path), force=force)
            if not stats.get("skipped"):
                total_conv += stats.get("conversations", 0)
                total_msg += stats.get("messages", 0)
            sources.append(stats.get("source_id"))
        except Exception as e:
            logger.error("Failed to ingest session %s: %s", path, e)

    return {
        "sources": len(sources),
        "conversations": total_conv,
        "messages": total_msg,
    }


def ingest_chatgpt_export(path: str, force: bool = False) -> Dict[str, Any]:
    """
    Ingest a ChatGPT data export (conversations.json or .zip containing it).
    Creates one Source, one Conversation per ChatGPT conversation thread.
    Returns stats: {source_id, conversations, messages, skipped}.
    """
    path = str(Path(path).resolve())
    source_hash = _file_hash(path)

    with db_session() as db:
        source, is_new = _get_or_create_source(
            db, "chatgpt_export", path, source_hash, force
        )
        if not is_new:
            return {"source_id": source.id, "skipped": True, "reason": "hash_unchanged"}

        parsed_convs = parse_chatgpt_export(path)

        total_messages = 0
        for pc in parsed_convs:
            conv = Conversation(
                source_id=source.id,
                external_id=pc.external_id,
                title=pc.title[:500] if pc.title else "ChatGPT Conversation",
                participant="chatgpt",
                started_at=pc.started_at,
                ended_at=pc.ended_at,
                message_count=len(pc.messages),
            )
            db.add(conv)
            db.flush()

            msg_objs = []
            for i, pm in enumerate(pc.messages):
                msg = Message(
                    conversation_id=conv.id,
                    ordinal=i,
                    role=pm.role,
                    content=pm.content[:32000] if pm.content else None,
                    raw_json={"model_slug": pm.model_slug, "uuid": pm.external_uuid},
                    external_uuid=pm.external_uuid or None,
                    char_offset_start=0,
                    char_offset_end=len(pm.content) if pm.content else 0,
                    sent_at=pm.sent_at,
                )
                msg_objs.append(msg)

            db.bulk_save_objects(msg_objs)
            total_messages += len(pc.messages)

        source.message_count = total_messages
        db.flush()

    return {
        "source_id": source.id,
        "conversations": len(parsed_convs),
        "messages": total_messages,
        "skipped": False,
    }


def ingest_shared_chat(
    directory: Optional[str] = None,
    limit: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Ingest AI Army shared chat markdown files in batches.
    Each batch becomes a single source+conversation.
    """
    directory = directory or settings.MW_SHARED_CHAT_DIR
    total_conv = total_msg = 0
    batch_num = 0

    for batch in batch_parse_shared_chat(directory, batch_size=settings.MW_BATCH_SIZE):
        if limit and total_msg >= limit:
            break

        # Use first file path as batch identifier
        batch_id = f"shared_chat_batch_{batch_num:04d}"
        batch_hash = hashlib.sha256(
            b"".join(m.source_path.encode() for m in batch)
        ).hexdigest()

        with db_session() as db:
            source, is_new = _get_or_create_source(
                db, "shared_chat", batch_id, batch_hash, force
            )
            if not is_new:
                batch_num += 1
                continue

            # Group by from/to pair → create conversations
            grouped: Dict[Tuple, list] = {}
            for msg in batch:
                key = (msg.from_agent, msg.to_agent)
                grouped.setdefault(key, []).append(msg)

            conv_count = 0
            msg_count = 0
            for (from_a, to_a), msgs in grouped.items():
                conv = Conversation(
                    source_id=source.id,
                    external_id=f"{from_a}_to_{to_a}_{batch_num}",
                    title=f"{from_a} → {to_a}",
                    participant=from_a,
                    started_at=msgs[0].sent_at,
                    ended_at=msgs[-1].sent_at,
                    message_count=len(msgs),
                )
                db.add(conv)
                db.flush()

                for i, m in enumerate(msgs):
                    content = f"[{m.subject}]\n{m.body}" if m.subject else m.body
                    msg_obj = Message(
                        conversation_id=conv.id,
                        ordinal=i,
                        role="user",
                        content=content[:32000],
                        raw_json={"from": m.from_agent, "to": m.to_agent, "subject": m.subject},
                        sent_at=m.sent_at,
                    )
                    db.add(msg_obj)
                    msg_count += 1

                conv_count += 1
                db.flush()

            source.message_count = msg_count
            total_conv += conv_count
            total_msg += msg_count
            batch_num += 1

    return {"batches": batch_num, "conversations": total_conv, "messages": total_msg}


def ingest_sqlite_memory(path: Optional[str] = None) -> Dict[str, Any]:
    """Import existing SQLite memory.db as memories directly."""
    path = path or settings.MW_SQLITE_MEMORY_PATH
    p = Path(path)
    if not p.exists():
        return {"error": f"File not found: {path}"}

    source_hash = _file_hash(path)

    with db_session() as db:
        source, is_new = _get_or_create_source(
            db, "sqlite_memory", str(p), source_hash, force=False
        )
        if not is_new:
            return {"source_id": source.id, "skipped": True}

        tables = set(list_tables(path))
        journal_tables = {"journal_entries", "journal", "entries", "messages"}
        knowledge_tables = {"knowledge_items", "knowledge", "facts", "memories"}
        journal = import_journal_entries(path) if tables & journal_tables else []
        knowledge = import_knowledge_items(path) if tables & knowledge_tables else []

        # Create one conversation to hold the imported memories as messages
        all_items = (
            [(j.text, j.category, j.created_at) for j in journal]
            + [(k.content, k.topic, k.created_at) for k in knowledge]
        )

        conv = Conversation(
            source_id=source.id,
            external_id="sqlite_memory_import",
            title="Imported SQLite Memory DB",
            participant="system",
            message_count=len(all_items),
        )
        db.add(conv)
        db.flush()

        for i, (content, category, created_at) in enumerate(all_items):
            msg = Message(
                conversation_id=conv.id,
                ordinal=i,
                role="assistant",
                content=content[:32000],
                raw_json={"category": category},
                sent_at=created_at,
            )
            db.add(msg)

        source.message_count = len(all_items)
        db.flush()

        return {
            "source_id": source.id,
            "journal_entries": len(journal),
            "knowledge_items": len(knowledge),
            "total_messages": len(all_items),
        }

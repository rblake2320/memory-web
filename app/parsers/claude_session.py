"""
Parse Claude Code session .jsonl files.

Each line is a JSON object with a message. Thread structure is preserved via
uuid/parentUuid fields. Parser streams the file so 200MB+ files don't blow memory.

Typical record shape:
{
  "uuid": "...",
  "parentUuid": "...",
  "type": "user" | "assistant" | "system",
  "message": {"role": "user", "content": "..."},
  "timestamp": "2025-01-01T00:00:00.000Z",
  "sessionId": "...",
  ...
}
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedMessage:
    external_uuid: str
    parent_uuid: Optional[str]
    role: str
    content: str
    sent_at: Optional[datetime]
    raw_json: dict
    char_offset_start: int
    char_offset_end: int
    token_count: Optional[int] = None


@dataclass
class ParsedConversation:
    external_id: str           # session ID or derived from filename
    source_path: str
    messages: List[ParsedMessage] = field(default_factory=list)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


def _extract_content(message: dict) -> str:
    """Extract text content from a message object (handles list or str)."""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    for sub in block.get("content", []):
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            parts.append(sub.get("text", ""))
                elif block.get("type") == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input", {})
                    parts.append(f"[Tool: {name}] {json.dumps(inp)[:200]}")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def parse_session_file(path: str) -> ParsedConversation:
    """
    Stream-parse a Claude session .jsonl file.
    Returns a ParsedConversation with all messages ordered by timestamp.
    """
    p = Path(path)
    session_id = p.stem          # filename without .jsonl

    messages: List[ParsedMessage] = []
    offset = 0

    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line_start = offset
                offset += len(line.encode("utf-8"))

                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed JSON line in %s", path)
                    continue

                # Determine role
                msg_type = record.get("type", "")
                inner = record.get("message", {})
                role = inner.get("role", msg_type) if inner else msg_type
                if role not in ("user", "assistant", "system", "tool"):
                    # Map Claude Code event types
                    if msg_type in ("user", "assistant", "system"):
                        role = msg_type
                    else:
                        continue  # skip non-message events

                content = _extract_content(inner if inner else record)
                if not content:
                    continue

                # Timestamp
                ts_raw = record.get("timestamp")
                sent_at: Optional[datetime] = None
                if ts_raw:
                    try:
                        sent_at = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except ValueError:
                        pass

                pm = ParsedMessage(
                    external_uuid=record.get("uuid", ""),
                    parent_uuid=record.get("parentUuid"),
                    role=role,
                    content=content,
                    sent_at=sent_at,
                    raw_json=record,
                    char_offset_start=line_start,
                    char_offset_end=offset,
                    token_count=None,
                )
                messages.append(pm)

    except OSError as e:
        logger.error("Cannot open session file %s: %s", path, e)
        raise

    # Sort by timestamp if available, otherwise keep file order
    messages.sort(key=lambda m: m.sent_at or datetime.min)

    started_at = messages[0].sent_at if messages else None
    ended_at = messages[-1].sent_at if messages else None

    return ParsedConversation(
        external_id=session_id,
        source_path=str(p),
        messages=messages,
        started_at=started_at,
        ended_at=ended_at,
    )


def iter_session_files(directory: str) -> Iterator[Path]:
    """Yield all .jsonl files in directory tree (recursive — Claude nests sessions in subdirs)."""
    d = Path(directory)
    if not d.exists():
        logger.warning("Sessions directory not found: %s", directory)
        return
    for f in sorted(d.rglob("*.jsonl")):
        yield f

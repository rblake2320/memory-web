"""
Parse ChatGPT data export conversations.json / .zip.

ChatGPT exports a ZIP file containing conversations.json (and other files).
conversations.json is a JSON array. Each element is a conversation with:
  - title       : str
  - create_time : float (Unix timestamp)
  - update_time : float (Unix timestamp)
  - mapping     : dict[uuid -> node]

Each node in mapping is:
  {
    "id": "uuid",
    "message": {
      "id": "uuid",
      "author": {"role": "user"|"assistant"|"system"|"tool"},
      "content": {"content_type": "text", "parts": ["..."]},
      "create_time": float | null,
      "metadata": {"model_slug": "gpt-4o", ...}
    },
    "parent": "uuid" | null,
    "children": ["uuid", ...]
  }

The mapping forms a tree (sometimes DAG when conversations branch). This
parser linearises it by following the primary branch (first child each time).
"""

import json
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class ChatGPTMessage:
    external_uuid: str
    role: str              # user | assistant | system | tool
    content: str
    sent_at: Optional[datetime]
    model_slug: Optional[str]


@dataclass
class ChatGPTConversation:
    title: str
    external_id: str       # conversation UUID
    messages: List[ChatGPTMessage] = field(default_factory=list)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


def _ts(t: Optional[float]) -> Optional[datetime]:
    """Unix float timestamp → datetime (UTC), tolerant of None."""
    try:
        return datetime.utcfromtimestamp(t) if t else None
    except (OSError, ValueError, OverflowError):
        return None


def _extract_content(message_data: dict) -> str:
    """
    Pull text from ChatGPT message content object.
    content_type can be: text, code, execution_output, tether_browsing_display,
    multimodal_text, etc. We extract what text we can.
    """
    content = message_data.get("content") or {}
    if isinstance(content, str):
        return content

    content_type = content.get("content_type", "text")

    if content_type in ("text", "code"):
        parts = content.get("parts") or []
        return "\n".join(str(p) for p in parts if isinstance(p, str) and p.strip())

    if content_type == "execution_output":
        return content.get("text", "")

    # Fallback: collect any string parts
    parts = content.get("parts") or []
    texts = []
    for p in parts:
        if isinstance(p, str) and p.strip():
            texts.append(p)
        elif isinstance(p, dict):
            if p.get("content_type") == "text":
                texts.append(p.get("text", ""))
    return "\n".join(texts)


def _linearize(mapping: Dict[str, dict]) -> List[dict]:
    """
    Walk the mapping DAG from root to leaves, returning a flat ordered list
    of message dicts. For branches, follows the first child (main thread).
    """
    if not mapping:
        return []

    # Root = node whose parent is absent from the mapping
    roots = [
        nid for nid, node in mapping.items()
        if not node.get("parent") or node["parent"] not in mapping
    ]
    if not roots:
        return []

    result: List[dict] = []
    visited: set = set()
    current: Optional[str] = roots[0]

    while current and current not in visited:
        visited.add(current)
        node = mapping.get(current)
        if not node:
            break
        msg = node.get("message")
        if msg:
            result.append(msg)
        children = node.get("children") or []
        current = children[0] if children else None

    return result


def _parse_json_data(data: list) -> List[ChatGPTConversation]:
    """Convert raw conversations array into ChatGPTConversation objects."""
    conversations: List[ChatGPTConversation] = []

    for conv_data in data:
        if not isinstance(conv_data, dict):
            continue

        title = conv_data.get("title") or "Untitled"
        conv_id = (
            conv_data.get("id")
            or conv_data.get("conversation_id")
            or f"chatgpt_{conv_data.get('create_time', '')}"
        )
        mapping = conv_data.get("mapping") or {}

        msg_datas = _linearize(mapping)
        messages: List[ChatGPTMessage] = []

        for msg_data in msg_datas:
            if not isinstance(msg_data, dict):
                continue
            author = msg_data.get("author") or {}
            role = author.get("role", "unknown")

            # Skip system messages and tool call scaffolding
            if role in ("system", "tool"):
                continue

            content = _extract_content(msg_data)
            if not content.strip():
                continue

            sent_at = _ts(msg_data.get("create_time"))
            model_slug = (msg_data.get("metadata") or {}).get("model_slug")

            messages.append(ChatGPTMessage(
                external_uuid=msg_data.get("id", ""),
                role=role,
                content=content,
                sent_at=sent_at,
                model_slug=model_slug,
            ))

        if not messages:
            continue

        conv = ChatGPTConversation(
            title=title,
            external_id=str(conv_id),
            messages=messages,
            started_at=messages[0].sent_at,
            ended_at=messages[-1].sent_at,
        )
        conversations.append(conv)

    return conversations


def parse_chatgpt_export(source: Union[str, Path, BytesIO]) -> List[ChatGPTConversation]:
    """
    Parse a ChatGPT export. Accepts:
    - Path to conversations.json
    - Path to a .zip containing conversations.json
    - BytesIO of either format

    Returns a list of ChatGPTConversation objects ready for ingestion.
    """
    data = None

    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as zf:
                names = zf.namelist()
                target = next(
                    (n for n in names if n.endswith("conversations.json")),
                    None,
                )
                if not target:
                    raise ValueError(
                        f"No conversations.json found in ZIP. Files: {names[:10]}"
                    )
                data = json.loads(zf.read(target))
        else:
            data = json.loads(p.read_text(encoding="utf-8"))

    elif isinstance(source, BytesIO):
        raw = source.read()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            source.seek(0)
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                names = zf.namelist()
                target = next(
                    (n for n in names if n.endswith("conversations.json")),
                    None,
                )
                if not target:
                    raise ValueError("No conversations.json found in ZIP")
                data = json.loads(zf.read(target))

    if not isinstance(data, list):
        raise ValueError(
            "Expected a JSON array of conversations. "
            "Make sure you're uploading conversations.json or the full ChatGPT export ZIP."
        )

    conversations = _parse_json_data(data)
    logger.info("Parsed %d ChatGPT conversations from export", len(conversations))
    return conversations

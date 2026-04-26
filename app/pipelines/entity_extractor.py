"""
Entity extractor: regex patterns (IPs, paths, models, services) + LLM hybrid.

Entity types:
- ip_address: IPv4/IPv6
- file_path: Unix/Windows paths
- model_name: AI model identifiers
- service: Known service names
- url: URLs
- person: Names (LLM only)
- project: Project identifiers (LLM)
- hostname: Machine hostnames
"""

import logging
import re
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from ..database import db_session
from ..models import Entity, EntityMention, Message, Segment
from ..services.ollama_client import generate_json

logger = logging.getLogger(__name__)

# --- Regex patterns ---
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PATH_UNIX_RE = re.compile(r"(?<!\w)(?:/[\w.\-]+){2,}")
_PATH_WIN_RE = re.compile(r"[A-Za-z]:\\[\w\\\-. ]+")
_MODEL_RE = re.compile(
    r"\b(?:gpt-[\w\-\.]+|claude-[\w\-\.]+|gemma[\w\-:\.]*|llama[\w\-:\.]*|"
    r"qwen[\w\-:\.]*|deepseek[\w\-:\.]*|mistral[\w\-:\.]*|"
    r"all-MiniLM[\w\-\.]*|sentence-transformers/[\w\-\.]+)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s\"'>]+")
_PORT_SERVICE_RE = re.compile(r"\b(?:localhost|127\.0\.0\.1):\d{4,5}\b")
_HOSTNAME_RE = re.compile(
    r"\b(?:spark-\d+|dgx-\d+|vps|node-[\w]+|worker-[\w]+)\b",
    re.IGNORECASE,
)


def _extract_regex_entities(text: str) -> List[Tuple[str, str, int, int]]:
    """Returns list of (name, entity_type, char_start, char_end)."""
    found = []
    for pattern, etype in [
        (_IP_RE, "ip_address"),
        (_PATH_UNIX_RE, "file_path"),
        (_PATH_WIN_RE, "file_path"),
        (_MODEL_RE, "model_name"),
        (_URL_RE, "url"),
        (_PORT_SERVICE_RE, "service"),
        (_HOSTNAME_RE, "hostname"),
    ]:
        for m in pattern.finditer(text):
            found.append((m.group(), etype, m.start(), m.end()))
    return found


def _llm_extract_entities(content: str) -> List[Tuple[str, str]]:
    """Returns list of (name, entity_type) from LLM extraction."""
    prompt = f"""
Extract named entities from this text. Return a JSON array where each element has:
{{"name": "entity name", "type": "person|project|service|tool|company|other"}}

Only extract clearly mentioned entities. Return an empty array if none found.

Text:
{content[:1500]}

Return ONLY the JSON array.
""".strip()

    try:
        result = generate_json(prompt)
        if isinstance(result, list):
            return [(r.get("name", ""), r.get("type", "other")) for r in result if r.get("name")]
    except Exception as e:
        logger.debug("LLM entity extraction failed: %s", e)

    return []


def _get_or_create_entity(db: Session, name: str, entity_type: str) -> Entity:
    canonical = name.lower().strip()
    entity = (
        db.query(Entity)
        .filter(Entity.canonical_name == canonical, Entity.entity_type == entity_type)
        .first()
    )
    if not entity:
        entity = Entity(
            name=name,
            entity_type=entity_type,
            canonical_name=canonical,
        )
        db.add(entity)
        db.flush()
    return entity


def extract_entities_for_segment(segment_id: int) -> int:
    """Extract entities for a segment. Returns number of mentions created."""
    with db_session() as db:
        seg = db.query(Segment).get(segment_id)
        if not seg:
            return 0

        # Skip if already done
        existing = db.query(EntityMention).filter(
            EntityMention.segment_id == segment_id
        ).count()
        if existing > 0:
            return 0

        messages = (
            db.query(Message)
            .filter(
                Message.conversation_id == seg.conversation_id,
                Message.ordinal >= seg.start_ordinal,
                Message.ordinal <= seg.end_ordinal,
                Message.tombstoned_at.is_(None),
            )
            .all()
        )

        all_content = "\n".join(m.content or "" for m in messages)
        mentions_created = 0

        # Regex entities (with positions)
        regex_entities = _extract_regex_entities(all_content)
        for name, etype, start, end in regex_entities:
            entity = _get_or_create_entity(db, name, etype)
            snippet = all_content[max(0, start - 40): end + 40]
            mention = EntityMention(
                entity_id=entity.id,
                segment_id=segment_id,
                char_start=start,
                char_end=end,
                context_snippet=snippet[:500],
            )
            db.add(mention)
            mentions_created += 1

        # LLM entities (no positions)
        llm_entities = _llm_extract_entities(all_content)
        for name, etype in llm_entities:
            if not name.strip():
                continue
            entity = _get_or_create_entity(db, name, etype)
            mention = EntityMention(
                entity_id=entity.id,
                segment_id=segment_id,
            )
            db.add(mention)
            mentions_created += 1

        db.flush()
        return mentions_created

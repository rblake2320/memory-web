"""
Multi-axis tagger: domain / intent / sensitivity / importance / project.

Single Ollama call per segment classifies all 5 axes with confidence scores.
"""

import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..database import db_session
from ..models import Message, Segment, Tag, TagAxis
from ..services.ollama_client import generate_json

logger = logging.getLogger(__name__)

AXES = {
    "domain": [
        "infrastructure", "ml", "web", "database", "security",
        "api", "devops", "data-science", "general", "other",
    ],
    "intent": [
        "debugging", "planning", "implementation", "learning",
        "review", "deployment", "research", "conversation", "other",
    ],
    "sensitivity": ["public", "internal", "confidential", "pii"],
    "importance": ["low", "medium", "high", "critical"],
    "project": [],  # free-form, any string
}


def _build_tag_prompt(content: str) -> str:
    return f"""
Classify this conversation segment using ALL five axes.
Return a JSON object with exactly these keys:

{{
  "domain": {{"value": "<from list>", "confidence": 0.0-1.0}},
  "intent": {{"value": "<from list>", "confidence": 0.0-1.0}},
  "sensitivity": {{"value": "public|internal|confidential|pii", "confidence": 0.0-1.0}},
  "importance": {{"value": "low|medium|high|critical", "confidence": 0.0-1.0}},
  "project": {{"value": "<project name or empty string>", "confidence": 0.0-1.0}}
}}

Domain options: {AXES['domain']}
Intent options: {AXES['intent']}

Conversation content:
{content[:2000]}

Return ONLY the JSON object.
""".strip()


def tag_segment(segment_id: int) -> int:
    """
    Tag a segment across all 5 axes. Returns number of tags created.
    """
    with db_session() as db:
        seg = db.query(Segment).get(segment_id)
        if not seg:
            return 0

        # Skip if already tagged
        existing_count = db.query(Tag).filter(Tag.segment_id == segment_id).count()
        if existing_count > 0:
            return 0

        # Get message content
        messages = (
            db.query(Message)
            .filter(
                Message.conversation_id == seg.conversation_id,
                Message.ordinal >= seg.start_ordinal,
                Message.ordinal <= seg.end_ordinal,
                Message.tombstoned_at.is_(None),
            )
            .order_by(Message.ordinal)
            .all()
        )

        if not messages:
            return 0

        content = "\n".join(
            f"[{m.role.upper()}]: {(m.content or '')[:500]}"
            for m in messages[:15]
        )

        # Fetch or cache axes
        axis_map: Dict[str, TagAxis] = {
            a.axis_name: a
            for a in db.query(TagAxis).all()
        }

        tags_created = 0
        try:
            result = generate_json(_build_tag_prompt(content))
            if not isinstance(result, dict):
                raise ValueError("Expected dict from LLM")

            for axis_name, axis_obj in axis_map.items():
                axis_data = result.get(axis_name, {})
                value = axis_data.get("value", "") if isinstance(axis_data, dict) else ""
                confidence = axis_data.get("confidence", 0.5) if isinstance(axis_data, dict) else 0.5

                if value and str(value).strip():
                    tag = Tag(
                        segment_id=segment_id,
                        axis_id=axis_obj.id,
                        value=str(value).strip()[:200],
                        confidence=float(confidence),
                    )
                    db.add(tag)
                    tags_created += 1

        except Exception as e:
            logger.warning("Tagging failed for segment %d: %s", segment_id, e)
            # Fall back: tag with defaults
            for axis_name, axis_obj in axis_map.items():
                default = "other" if axis_name in ("domain", "intent") else (
                    "internal" if axis_name == "sensitivity" else (
                        "medium" if axis_name == "importance" else ""
                    )
                )
                if default:
                    tag = Tag(
                        segment_id=segment_id,
                        axis_id=axis_obj.id,
                        value=default,
                        confidence=0.1,
                    )
                    db.add(tag)
                    tags_created += 1

        db.flush()
        return tags_created


def tag_conversation_segments(conversation_id: int) -> int:
    """Tag all segments in a conversation. Returns total tags created."""
    with db_session() as db:
        seg_ids = [
            s.id for s in
            db.query(Segment).filter(Segment.conversation_id == conversation_id).all()
        ]

    total = 0
    for seg_id in seg_ids:
        total += tag_segment(seg_id)
    return total

"""
Topic segmentation pipeline.

Step 1: Heuristic split on time gaps (> 30min) or message count (> 20).
Step 2: LLM refinement via qwen2.5-coder:32b to merge/split on topic shifts.

Returns a list of segment boundaries: [(start_ordinal, end_ordinal, summary)]
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from ..config import settings
from ..database import db_session
from ..models import Conversation, Message, Segment
from ..services.ollama_client import generate_json

logger = logging.getLogger(__name__)

HEURISTIC_GAP = timedelta(minutes=settings.MW_SEGMENT_GAP_MINUTES)
MAX_MSGS = settings.MW_SEGMENT_MAX_MESSAGES

SegBoundary = Tuple[int, int, Optional[str]]  # (start_ordinal, end_ordinal, summary)


def heuristic_segment(messages: List[Message]) -> List[SegBoundary]:
    """Split messages into segments based on time gaps and max count."""
    if not messages:
        return []

    segments: List[SegBoundary] = []
    seg_start = messages[0].ordinal
    seg_count = 1
    prev_ts: Optional[datetime] = messages[0].sent_at

    for msg in messages[1:]:
        cur_ts = msg.sent_at
        time_gap = (
            (cur_ts - prev_ts) > HEURISTIC_GAP
            if (cur_ts and prev_ts)
            else False
        )
        count_exceeded = seg_count >= MAX_MSGS

        if time_gap or count_exceeded:
            segments.append((seg_start, messages[messages.index(msg) - 1].ordinal, None))
            seg_start = msg.ordinal
            seg_count = 1
        else:
            seg_count += 1

        prev_ts = cur_ts

    # Last segment
    segments.append((seg_start, messages[-1].ordinal, None))
    return segments


def llm_refine_and_summarise(
    messages: List[Message],
    boundaries: List[SegBoundary],
) -> List[SegBoundary]:
    """
    Ask Ollama to:
    1. Validate/refine boundaries (merge micro-segments, split topic shifts)
    2. Generate a short summary for each segment
    Returns updated boundaries with summaries.
    """
    if not boundaries:
        return boundaries

    # Build compact text for LLM
    msg_map = {m.ordinal: m for m in messages}
    segments_text = []
    for start, end, _ in boundaries:
        msgs_in_seg = [msg_map[o] for o in range(start, end + 1) if o in msg_map]
        combined = " | ".join(
            f"[{m.role.upper()}]: {(m.content or '')[:300]}"
            for m in msgs_in_seg[:10]
        )
        segments_text.append({"start": start, "end": end, "preview": combined})

    prompt = f"""
You are a conversation analyst. Given these conversation segment previews,
return a JSON list where each element has:
- "start": start ordinal (integer, same as input)
- "end": end ordinal (integer, same as input)
- "summary": 1-2 sentence description of the topic

Only return a JSON array. No explanation. Use the exact start/end values provided.

Input segments:
{segments_text}
""".strip()

    try:
        refined = generate_json(prompt)
        if isinstance(refined, list):
            result = []
            for item in refined:
                start = item.get("start")
                end = item.get("end")
                summary = item.get("summary", "")
                if start is not None and end is not None:
                    result.append((int(start), int(end), summary))
            return result if result else boundaries
    except Exception as e:
        logger.warning("LLM segmentation refinement failed: %s — using heuristics", e)

    return boundaries


def segment_conversation(conversation_id: int, use_llm: bool = True) -> int:
    """
    Segment a conversation. Returns number of segments created.
    Skips if segments already exist.
    """
    with db_session() as db:
        conv = db.query(Conversation).get(conversation_id)
        if not conv:
            logger.error("Conversation %d not found", conversation_id)
            return 0

        # Skip if already segmented
        existing = db.query(Segment).filter(
            Segment.conversation_id == conversation_id
        ).count()
        if existing > 0:
            logger.debug("Conversation %d already segmented (%d segments) — skipping", conversation_id, existing)
            return 0

        messages = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .filter(Message.tombstoned_at.is_(None))
            .order_by(Message.ordinal)
            .all()
        )

        if not messages:
            return 0

        boundaries = heuristic_segment(messages)

        if use_llm and len(boundaries) > 1:
            boundaries = llm_refine_and_summarise(messages, boundaries)

        msg_by_ordinal = {m.ordinal: m for m in messages}

        for start_ord, end_ord, summary in boundaries:
            start_msg = msg_by_ordinal.get(start_ord)
            end_msg = msg_by_ordinal.get(end_ord)

            if not start_msg or not end_msg:
                continue

            count = end_ord - start_ord + 1
            seg = Segment(
                conversation_id=conversation_id,
                start_message_id=start_msg.id,
                end_message_id=end_msg.id,
                start_ordinal=start_ord,
                end_ordinal=end_ord,
                summary=summary,
                model_used=settings.MW_OLLAMA_MODEL if use_llm else None,
                message_count=count,
            )
            db.add(seg)

        db.flush()

        count_created = db.query(Segment).filter(
            Segment.conversation_id == conversation_id
        ).count()
        return count_created

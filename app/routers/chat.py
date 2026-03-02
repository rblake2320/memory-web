"""Chat API — memory-grounded conversational endpoint."""

import json
import logging
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import settings
from ..services import retrieval

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

_SETTINGS_FILE = Path(__file__).parent.parent.parent / ".env.local"


def _load_settings() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []
    provider: Optional[str] = None   # override settings; None = use saved setting


class ChatResponse(BaseModel):
    reply: str
    memories_used: int
    provider_used: str


def _build_system_prompt(memories: List[Any]) -> str:
    if not memories:
        return (
            "You are MemoryWeb Assistant, a helpful AI with access to the user's personal knowledge base. "
            "No relevant memories were found for this query."
        )
    mem_text = "\n\n".join(
        f"[Memory {i+1}] (score={r.score:.2f}): {r.content}"
        for i, r in enumerate(memories)
    )
    return (
        "You are MemoryWeb Assistant, a helpful AI with access to the user's personal knowledge base.\n\n"
        "The following memories are relevant to the current question:\n\n"
        f"{mem_text}\n\n"
        "Use these memories to inform your answer. Be concise and specific. "
        "If a memory is directly relevant, reference it. "
        "If the memories don't contain the answer, say so honestly."
    )


async def _call_ollama(messages: List[dict], system_prompt: str, model: str) -> str:
    import httpx
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{settings.MW_OLLAMA_BASE_URL}/api/chat", json=payload)
        r.raise_for_status()
        return r.json()["message"]["content"]


async def _call_claude(messages: List[dict], system_prompt: str, model: str, api_key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text


async def _call_openai(messages: List[dict], system_prompt: str, model: str, api_key: str) -> str:
    import openai
    client = openai.AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_prompt}] + messages,
        max_tokens=2048,
    )
    return resp.choices[0].message.content


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest):
    """Chat using memories as grounded knowledge base."""
    cfg = _load_settings()
    provider = body.provider or cfg.get("provider", "ollama")

    # 1. Retrieve relevant memories
    try:
        result = retrieval.search(
            query=body.message,
            filters=None,
            k=10,
            include_tombstoned=False,
            min_tier=1,
            force_tier=None,
        )
        memories = result.results
    except Exception as e:
        logger.warning("Memory retrieval failed: %s", e)
        memories = []

    system_prompt = _build_system_prompt(memories)
    history_msgs = [{"role": m.role, "content": m.content} for m in body.history]
    history_msgs.append({"role": "user", "content": body.message})

    # 2. Call LLM provider
    try:
        if provider == "claude":
            api_key = cfg.get("claude_api_key", "")
            if not api_key:
                return ChatResponse(
                    reply="Claude API key not configured. Go to Settings and add your key.",
                    memories_used=len(memories),
                    provider_used="claude",
                )
            model = cfg.get("model", "claude-sonnet-4-6")
            reply = await _call_claude(history_msgs, system_prompt, model, api_key)

        elif provider == "openai":
            api_key = cfg.get("openai_api_key", "")
            if not api_key:
                return ChatResponse(
                    reply="OpenAI API key not configured. Go to Settings and add your key.",
                    memories_used=len(memories),
                    provider_used="openai",
                )
            model = cfg.get("model", "gpt-4o-mini")
            reply = await _call_openai(history_msgs, system_prompt, model, api_key)

        else:  # ollama (default)
            model = cfg.get("ollama_model", settings.MW_OLLAMA_MODEL)
            reply = await _call_ollama(history_msgs, system_prompt, model)

    except Exception as e:
        logger.error("LLM call failed (provider=%s): %s", provider, e)
        reply = f"LLM error ({provider}): {e}"

    return ChatResponse(reply=reply, memories_used=len(memories), provider_used=provider)

"""Settings API — store/retrieve LLM provider config and API keys."""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])

_SETTINGS_FILE = Path(__file__).parent.parent.parent / ".env.local"


def _load() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict):
    _SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _mask(key: Optional[str]) -> Optional[str]:
    if not key or len(key) < 8:
        return None
    return key[:4] + "..." + key[-4:]


class SettingsIn(BaseModel):
    provider: Optional[str] = None          # "ollama" | "claude" | "openai"
    model: Optional[str] = None
    claude_api_key: Optional[str] = None    # full key; omit to keep existing
    openai_api_key: Optional[str] = None    # full key; omit to keep existing
    ollama_model: Optional[str] = None


class SettingsOut(BaseModel):
    provider: str = "ollama"
    model: str = "qwen2.5-coder:32b"
    claude_api_key_masked: Optional[str] = None
    openai_api_key_masked: Optional[str] = None
    ollama_model: str = "qwen2.5-coder:32b"


@router.get("", response_model=SettingsOut)
def get_settings():
    """Return current settings with API keys masked."""
    d = _load()
    return SettingsOut(
        provider=d.get("provider", "ollama"),
        model=d.get("model", "qwen2.5-coder:32b"),
        claude_api_key_masked=_mask(d.get("claude_api_key")),
        openai_api_key_masked=_mask(d.get("openai_api_key")),
        ollama_model=d.get("ollama_model", "qwen2.5-coder:32b"),
    )


@router.post("", response_model=SettingsOut)
def save_settings(body: SettingsIn):
    """Persist settings. Pass full API key to update it; omit to keep existing."""
    d = _load()
    if body.provider is not None:
        d["provider"] = body.provider
    if body.model is not None:
        d["model"] = body.model
    if body.ollama_model is not None:
        d["ollama_model"] = body.ollama_model
    if body.claude_api_key:
        d["claude_api_key"] = body.claude_api_key
    if body.openai_api_key:
        d["openai_api_key"] = body.openai_api_key
    _save(d)
    logger.info("Settings saved (provider=%s, model=%s)", d.get("provider"), d.get("model"))
    return SettingsOut(
        provider=d.get("provider", "ollama"),
        model=d.get("model", "qwen2.5-coder:32b"),
        claude_api_key_masked=_mask(d.get("claude_api_key")),
        openai_api_key_masked=_mask(d.get("openai_api_key")),
        ollama_model=d.get("ollama_model", "qwen2.5-coder:32b"),
    )

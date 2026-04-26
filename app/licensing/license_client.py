"""
License validation client.

Validates MW_LICENSE_KEY against the license server on startup.
Caches the result for 48 hours. Degrades gracefully if server unreachable.

Grace period: Normal → Read-only warning (48h) → Locked (403 on Pro writes)

IMPORTANT: License enforcement NEVER causes silent data loss.
Even in locked state, Community features (ingest, search, memories) remain available.
The locked state only blocks Pro features.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / ".license_cache"
_CACHE_TTL = 48 * 3600  # 48 hours
_GRACE_PERIOD = 48 * 3600  # 48h before degrading to locked

_lock = threading.Lock()


@dataclass
class LicenseState:
    is_valid: bool = False
    plan: str = "community"
    features: list = field(default_factory=list)
    expires_at: Optional[float] = None  # Unix timestamp
    validated_at: float = field(default_factory=time.time)
    error: Optional[str] = None
    grace_until: Optional[float] = None  # Unix timestamp for grace period end

    @property
    def is_in_grace_period(self) -> bool:
        if self.grace_until is None:
            return False
        return time.time() < self.grace_until

    @property
    def mode(self) -> str:
        """Returns: 'community' | 'pro' | 'grace' | 'locked'"""
        from ..config import settings
        if not settings.MW_LICENSE_KEY:
            return "community"
        if self.is_valid:
            return self.plan
        if self.is_in_grace_period:
            return "grace"
        return "community"  # degraded to community, not locked — never block Community features


# Module-level state
_state: Optional[LicenseState] = None
_last_validated: float = 0.0


def _load_cache() -> Optional[LicenseState]:
    """Load cached license state from disk."""
    try:
        if not _CACHE_FILE.exists():
            return None
        data = json.loads(_CACHE_FILE.read_text())
        validated_at = data.get("validated_at", 0)
        if time.time() - validated_at > _CACHE_TTL:
            return None  # Cache expired
        return LicenseState(
            is_valid=data.get("is_valid", False),
            plan=data.get("plan", "community"),
            features=data.get("features", []),
            expires_at=data.get("expires_at"),
            validated_at=validated_at,
            grace_until=data.get("grace_until"),
        )
    except Exception:
        return None


def _save_cache(state: LicenseState) -> None:
    """Persist license state to disk."""
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({
            "is_valid": state.is_valid,
            "plan": state.plan,
            "features": state.features,
            "expires_at": state.expires_at,
            "validated_at": state.validated_at,
            "grace_until": state.grace_until,
        }))
    except Exception as e:
        logger.debug("License cache write failed: %s", e)


def validate_license(license_key: str, server_url: str) -> LicenseState:
    """
    Validate license key against the license server.
    Returns LicenseState. Never raises — always returns a valid state object.
    """
    try:
        import httpx
        resp = httpx.post(
            f"{server_url}/v1/validate",
            json={"key": license_key},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            state = LicenseState(
                is_valid=True,
                plan=data.get("plan", "pro"),
                features=data.get("features", list(PRO_FEATURES)),
                expires_at=data.get("exp"),
                validated_at=time.time(),
            )
            _save_cache(state)
            return state
        elif resp.status_code == 402:
            # License expired — start grace period
            cached = _load_cache()
            grace_until = time.time() + _GRACE_PERIOD
            state = LicenseState(
                is_valid=False,
                plan=cached.plan if cached else "community",
                error="expired",
                validated_at=time.time(),
                grace_until=grace_until,
            )
            _save_cache(state)
            return state
        else:
            logger.warning("License validation returned %d", resp.status_code)
    except Exception as e:
        logger.debug("License server unreachable: %s", e)
        # Network failure — use cache with grace period
        cached = _load_cache()
        if cached and cached.is_valid:
            # Extend grace period if we have a valid cache
            cached.grace_until = time.time() + _GRACE_PERIOD
            return cached

    # No cache, no server — community mode
    return LicenseState(is_valid=False, plan="community", validated_at=time.time())


def get_license_state() -> LicenseState:
    """
    Get current license state. Validates on first call; uses cache thereafter.
    Thread-safe.
    """
    global _state, _last_validated

    from ..config import settings

    if not settings.MW_LICENSE_KEY:
        return LicenseState(is_valid=True, plan="community", validated_at=time.time())

    with _lock:
        now = time.time()
        if _state is None or (now - _last_validated) > _CACHE_TTL:
            _state = validate_license(settings.MW_LICENSE_KEY, settings.MW_LICENSE_SERVER)
            _last_validated = now

    return _state


# Lazy import inside function to avoid circular import
try:
    from .feature_flags import PRO_FEATURES
except ImportError:
    PRO_FEATURES = frozenset()

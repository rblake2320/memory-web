"""
Feature flags and Pro feature gating.
"""
from typing import Optional
from fastapi import Depends, HTTPException, status

from ..config import settings

# Features available to all users (Community Edition)
COMMUNITY_FEATURES = frozenset({
    "search",
    "ingest",
    "memories",
    "answer_certificates",
    "event_log_chain",
    "contradiction_detection",
    "belief_recomputation",
    "source_invalidation",
    "retention",
    "chat_injection",
})

# Features requiring a valid Pro license
PRO_FEATURES = frozenset({
    "chat_providers",       # Multiple LLM provider integrations
    "data_export",          # Bulk export connectors (CSV, JSON, Parquet)
    "custom_embeddings",    # Custom embedding model support
    "admin_analytics",      # Advanced pipeline analytics dashboard
    "multi_instance",       # Multiple instance orchestration
    "hosted_backups",       # Automated backup management
})

# All features
ALL_FEATURES = COMMUNITY_FEATURES | PRO_FEATURES


def is_pro() -> bool:
    """Check if this instance is running with a valid Pro license."""
    if not settings.MW_LICENSE_KEY:
        return False
    try:
        from .license_client import get_license_state
        state = get_license_state()
        return state.is_valid and state.plan in ("pro", "team", "managed")
    except Exception:
        return False


def require_pro(feature: str):
    """
    FastAPI dependency factory for Pro feature gates.

    Usage:
        @router.get("/export")
        def export(
            _: None = Depends(require_pro("data_export"))
        ):
            ...

    Community users get 403 with a clear upgrade message.
    """
    async def _check():
        if feature in COMMUNITY_FEATURES:
            return  # Always available
        if feature not in PRO_FEATURES:
            return  # Unknown feature — allow (future-proofing)

        # No license key → community mode
        if not settings.MW_LICENSE_KEY:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "pro_required",
                    "feature": feature,
                    "message": f"'{feature}' requires a Memory Beast Pro license. "
                               f"See https://memorybeast.app/pricing",
                    "upgrade_url": "https://memorybeast.app/pricing",
                },
            )

        # License key set — validate
        if not is_pro():
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "error": "license_invalid",
                    "feature": feature,
                    "message": "Your Pro license is invalid or expired. "
                               "Please renew at https://memorybeast.app/account",
                    "renew_url": "https://memorybeast.app/account",
                },
            )

    return _check

"""
License enforcement module for Memory Beast.

Community Edition: MW_LICENSE_KEY empty → all features work (owner/community mode).
Pro Edition: MW_LICENSE_KEY set → validated against license server → unlocks pro features.

Feature gates (require_pro dependency):
- chat_providers: multiple LLM provider integrations
- data_export: bulk export connectors
- custom_embeddings: custom embedding model support
- admin_analytics: advanced pipeline analytics
- multi_instance: multiple instance orchestration
- hosted_backups: automated backup management

Community features (always free):
- answer_certificates: provenance audit trail
- event_log_chain: tamper-evident hash chain
- 3-tier retrieval
- contradiction detection
- belief recomputation
- source invalidation

The trust primitives (answer_certificates, event_log_chain) are intentionally NOT
gated — they are what makes Memory Beast trustworthy, not an upsell.
"""

from .feature_flags import COMMUNITY_FEATURES, PRO_FEATURES, require_pro, is_pro
from .license_client import LicenseState, get_license_state

__all__ = [
    "COMMUNITY_FEATURES",
    "PRO_FEATURES",
    "require_pro",
    "is_pro",
    "LicenseState",
    "get_license_state",
]

"""Optional, opt-in AI assist layer (Phase 2).

Runs only on already-decided results. Forbidden from setting any status;
the deterministic engine owns every verdict. Degrades gracefully when no
API key is configured or a response cannot be parsed.
"""

from plumb.ai.client import AIClient, AIUnavailable, cortex_enabled, get_client
from plumb.ai.explain import attach_explanations, explain_failure
from plumb.ai.fix import draft_fix
from plumb.ai.recon_sql import draft_recon_sql

__all__ = [
    "AIClient",
    "AIUnavailable",
    "cortex_enabled",
    "get_client",
    "attach_explanations",
    "explain_failure",
    "draft_fix",
    "draft_recon_sql",
]

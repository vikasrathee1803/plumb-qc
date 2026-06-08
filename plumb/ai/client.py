"""Optional assist via Snowflake Cortex.

Plumb's assist layer is purely explanatory: it never sets a check status,
severity, or verdict, so a run is identical with or without it except for the
ai_explanation text. The only model path is Snowflake Cortex, called through
the same read-only session the checks already use:

    SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?) AS response

That is a single SELECT, so it satisfies the read-only invariant, and no data
leaves Snowflake. It runs in-database, so there is no external API key.

Placeholder: this is wired but off by default while Cortex access is being set
up. Set PLUMB_CORTEX_MODEL (for example "llama3.1-70b") and run live to enable
it. The completion callable stays injectable so the assist functions are
testable without a warehouse.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Protocol

CORTEX_MODEL_ENV = "PLUMB_CORTEX_MODEL"
DEFAULT_CORTEX_MODEL = "llama3.1-70b"

CompleteFn = Callable[[str, str, int], str]


class _Session(Protocol):
    def execute(self, sql: str, params: Any = None) -> Any: ...


class AIUnavailable(Exception):
    """Cortex assist is not available: it is disabled, or there is no live
    Snowflake session. Callers treat this as a graceful skip, never as a
    verdict failure."""


def cortex_enabled() -> bool:
    """True when Cortex assist has been turned on via PLUMB_CORTEX_MODEL."""
    return bool(os.environ.get(CORTEX_MODEL_ENV))


class AIClient:
    def __init__(
        self,
        *,
        session: _Session | None = None,
        model: str | None = None,
        complete: CompleteFn | None = None,
    ) -> None:
        if complete is not None:
            # Test or custom transport: no session or model needed.
            self.provider = "injected"
            self.model = model or "injected-model"
            self._complete = complete
            return
        if model is None and not cortex_enabled():
            raise AIUnavailable(
                f"Snowflake Cortex assist is off; set {CORTEX_MODEL_ENV} to enable it"
            )
        if session is None:
            raise AIUnavailable(
                "Cortex assist needs a live Snowflake session; run live, not static-only"
            )
        self.provider = "cortex"
        self.model = model or os.environ.get(CORTEX_MODEL_ENV) or DEFAULT_CORTEX_MODEL
        self._session = session
        self._complete = self._cortex_complete

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        return self._complete(system, user, max_tokens)

    def _cortex_complete(self, system: str, user: str, max_tokens: int) -> str:
        prompt = f"{system}\n\n{user}"
        result = self._session.execute(
            "SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?) AS response", (self.model, prompt)
        )
        rows = getattr(result, "rows", result)
        if not rows:
            return ""
        row = rows[0]
        if isinstance(row, dict):
            return str(next(iter(row.values()), "") or "")
        return str(row[0] or "")


def get_client(*, session: Any = None, **kwargs: Any) -> AIClient | None:
    """Return a Cortex assist client, or None when assist is disabled or no
    live session is available. Never raises, so a surface can offer assist as
    a graceful add-on."""
    try:
        return AIClient(session=session, **kwargs)
    except AIUnavailable:
        return None

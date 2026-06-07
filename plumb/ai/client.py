"""Anthropic SDK wrapper for the opt-in assist layer.

The API key comes from the environment or the OS keychain, never the repo.
The completion callable is injectable so the assist functions are fully
testable without a network call. This layer is forbidden from setting any
check status; it only produces text that callers attach to a decided
result.
"""

from __future__ import annotations

import os
from typing import Callable

import keyring
import keyring.errors

KEYRING_SERVICE = "plumb"
ENV_API_KEY = "ANTHROPIC_API_KEY"
ENV_MODEL = "PLUMB_AI_MODEL"
DEFAULT_MODEL = "claude-sonnet-4-6"

CompleteFn = Callable[[str, str, int], str]


class AIUnavailable(Exception):
    """No API key configured, so the assist layer cannot run. Callers treat
    this as a graceful skip, never as a verdict failure."""


def _resolve_api_key(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    env = os.environ.get(ENV_API_KEY)
    if env:
        return env
    try:
        return keyring.get_password(KEYRING_SERVICE, "anthropic_api_key")
    except keyring.errors.KeyringError:
        return None


class AIClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        complete: CompleteFn | None = None,
    ) -> None:
        self.model = model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL
        if complete is not None:
            self._complete = complete
            self._api_key = api_key
            return
        self._api_key = _resolve_api_key(api_key)
        if not self._api_key:
            raise AIUnavailable(
                f"no Anthropic API key; set {ENV_API_KEY} or store it in the OS "
                f"keychain under service {KEYRING_SERVICE!r}, or omit --explain"
            )
        self._complete = self._anthropic_complete

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        return self._complete(system, user, max_tokens)

    def _anthropic_complete(self, system: str, user: str, max_tokens: int) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = [
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", None) == "text"
        ]
        return "".join(parts)


def get_client(**kwargs: object) -> AIClient | None:
    """Return a client, or None if no key is configured. Never raises so a
    surface can offer assist as a graceful add-on."""
    try:
        return AIClient(**kwargs)  # type: ignore[arg-type]
    except AIUnavailable:
        return None

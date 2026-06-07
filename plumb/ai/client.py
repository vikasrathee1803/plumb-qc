"""LLM client for the opt-in assist layer. Groq-first, multi-provider.

Provider is chosen by the first key present, mirroring the job-assistant
llm.py convention: Groq (OpenAI-compatible), then xAI, then Gemini, then
Anthropic. Groq is the default and preferred path. Keys come from the
environment or the OS keychain, never the repo. The completion callable is
injectable so the assist functions are fully testable without a network.
This layer is forbidden from setting any check status.
"""

from __future__ import annotations

import os
from typing import Callable

import keyring
import keyring.errors

KEYRING_SERVICE = "plumb"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
XAI_BASE_URL = "https://api.x.ai/v1"

# Default model per provider. Override with PLUMB_AI_MODEL.
_DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "xai": "grok-2-1212",
    "gemini": "gemini-2.0-flash",
    "anthropic": "claude-haiku-4-5-20251001",
}

# Env var that holds each provider's key, in precedence order.
_PROVIDER_ENV = [
    ("groq", "GROQ_API_KEY"),
    ("xai", "XAI_API_KEY"),
    ("gemini", "GEMINI_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
]

CompleteFn = Callable[[str, str, int], str]


class AIUnavailable(Exception):
    """No provider key configured, so the assist layer cannot run. Callers
    treat this as a graceful skip, never as a verdict failure."""


def _keyring_key(entry: str) -> str | None:
    try:
        return keyring.get_password(KEYRING_SERVICE, entry)
    except keyring.errors.KeyringError:
        return None


def _resolve_provider() -> tuple[str, str]:
    """Return (provider, api_key) for the first key present in env or
    keychain, Groq first."""
    for provider, env_var in _PROVIDER_ENV:
        key = os.environ.get(env_var) or _keyring_key(env_var.lower())
        if key:
            return provider, key.strip()
    raise AIUnavailable(
        "no LLM API key found; set GROQ_API_KEY (preferred) or one of "
        "XAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY, in the environment "
        "or the OS keychain, or omit --explain"
    )


class AIClient:
    def __init__(
        self,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        complete: CompleteFn | None = None,
    ) -> None:
        if complete is not None:
            # Test or custom transport: no key needed.
            self.provider = provider or "injected"
            self._api_key = api_key
            self.model = model or "injected-model"
            self._complete = complete
            return
        if provider and api_key:
            self.provider, self._api_key = provider, api_key
        else:
            self.provider, self._api_key = _resolve_provider()
        self.model = model or os.environ.get("PLUMB_AI_MODEL") or _DEFAULT_MODELS[self.provider]
        self._complete = self._provider_complete

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        return self._complete(system, user, max_tokens)

    def _provider_complete(self, system: str, user: str, max_tokens: int) -> str:
        if self.provider in ("groq", "xai"):
            return self._openai_compatible(system, user, max_tokens)
        if self.provider == "gemini":
            return self._gemini(system, user, max_tokens)
        if self.provider == "anthropic":
            return self._anthropic(system, user, max_tokens)
        raise AIUnavailable(f"unsupported provider: {self.provider}")

    def _openai_compatible(self, system: str, user: str, max_tokens: int) -> str:
        from openai import OpenAI

        base = GROQ_BASE_URL if self.provider == "groq" else XAI_BASE_URL
        client = OpenAI(api_key=self._api_key, base_url=base)
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""

    def _gemini(self, system: str, user: str, max_tokens: int) -> str:
        import google.generativeai as genai

        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(model_name=self.model, system_instruction=system)
        resp = model.generate_content(
            user,
            generation_config={
                "max_output_tokens": max_tokens,
                "temperature": 0.3,
                "response_mime_type": "application/json",
            },
        )
        return resp.text or ""

    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
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

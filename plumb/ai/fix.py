"""Draft a minimal fix for a failed check. Advisory only: the returned
patch is never applied and never affects a status."""

from __future__ import annotations

import json
from typing import Any

from plumb.ai.client import AIClient
from plumb.ai.parser import extract_json
from plumb.ai.prompts import FIX_SYSTEM

FIX_MAX_TOKENS = 500


def draft_fix(client: AIClient, check: Any, sql_text: str | None) -> dict[str, Any] | None:
    payload = {
        "check_id": check.id,
        "check_name": check.name,
        "observed": check.observed,
        "expected": check.expected,
        "sql_context": (sql_text or "")[:4000],
    }
    raw = client.complete(FIX_SYSTEM, json.dumps(payload, default=str), FIX_MAX_TOKENS)
    data = extract_json(raw)
    if data is None or "patch" not in data:
        return None
    data.setdefault("needs_human_review", True)
    return data

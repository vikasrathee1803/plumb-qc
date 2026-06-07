"""Draft reconciliation SQL from a plain-English intent.

Produces a single scalar aggregate to use as a source-of-truth query for
D-RECON-001. The analyst reviews and pastes it into a ruleset; it is never
executed automatically and never sets a status. If the intent is ambiguous
the model returns null sql plus a blocking question.
"""

from __future__ import annotations

import json
from typing import Any

from plumb.ai.client import AIClient
from plumb.ai.parser import extract_json
from plumb.ai.prompts import RECON_SYSTEM

RECON_MAX_TOKENS = 500


def draft_recon_sql(
    client: AIClient, intent: str, objects: list[str] | None = None
) -> dict[str, Any] | None:
    payload = {"intent": intent, "named_objects": objects or []}
    raw = client.complete(RECON_SYSTEM, json.dumps(payload, default=str), RECON_MAX_TOKENS)
    data = extract_json(raw)
    if data is None or "sql" not in data:
        return None
    data.setdefault("assumptions", [])
    data.setdefault("blocking_question", None)
    return data

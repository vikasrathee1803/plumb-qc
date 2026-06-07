"""Explain a failed check, and attach explanations to a decided RunResult.

attach_explanations mutates only CheckResult.ai_explanation. It never reads
or writes any status, severity, or verdict, so a run is bit-for-bit
identical with and without it except for that one text field. Any failure
(no key, network error, bad JSON) degrades to leaving ai_explanation None.
"""

from __future__ import annotations

import json
from typing import Any

from plumb.ai.client import AIClient
from plumb.ai.parser import extract_json
from plumb.ai.prompts import EXPLAIN_SYSTEM
from plumb.engine.models import RunResult, Status

EXPLAIN_MAX_TOKENS = 300
_EXPLAINABLE = {Status.FAIL, Status.ERROR, Status.WARN}


def _user_message(check: Any, sql_text: str | None) -> str:
    payload = {
        "check_id": check.id,
        "check_name": check.name,
        "severity": check.severity.value,
        "status": check.status.value,
        "observed": check.observed,
        "expected": check.expected,
        "evidence_sample": check.evidence.sample_rows[:5],
        "sql_context": (sql_text or "")[:2000],
    }
    return json.dumps(payload, default=str)


def explain_failure(client: AIClient, check: Any, sql_text: str | None) -> dict[str, Any] | None:
    raw = client.complete(EXPLAIN_SYSTEM, _user_message(check, sql_text), EXPLAIN_MAX_TOKENS)
    data = extract_json(raw)
    if not data or "root_cause" not in data:
        return None
    return data


def _format(explanation: dict[str, Any]) -> str:
    root = str(explanation.get("root_cause", "")).strip()
    impact = str(explanation.get("business_impact", "")).strip()
    confidence = str(explanation.get("confidence", "")).strip()
    text = root
    if impact:
        text = f"{text} {impact}".strip()
    if confidence:
        text = f"{text} (confidence: {confidence})"
    return text


def attach_explanations(
    result: RunResult, client: AIClient, sql_text: str | None = None
) -> RunResult:
    """Attach AI explanations to failing checks in place. Returns the same
    result. Statuses, severities, summary, coverage, and verdict are never
    touched."""
    for check in result.checks:
        if check.status not in _EXPLAINABLE:
            continue
        try:
            explanation = explain_failure(client, check, sql_text)
        except Exception:  # noqa: BLE001 - assist must never break a run
            explanation = None
        if explanation is not None:
            check.ai_explanation = _format(explanation)
    return result

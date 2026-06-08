"""Evidence capping and PII redaction.

Every sample row that lands in a CheckResult's evidence goes through
prepare_evidence_rows. Caps and redaction are applied before anything is
stored or rendered, so no surface can leak raw rows by accident. With
aggregate_only set, no row samples are produced at all.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from plumb.config.models import Ruleset

REDACTED = "[redacted]"

# Content detectors: redact a value when it looks like PII regardless of the
# column name, so a card number in a "memo" column is still caught. Catching
# a non-PII value that happens to pass Luhn over-redacts, which is the safe
# direction for a regulated deployment.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_PAN_CANDIDATE_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _looks_like_pii(value: Any) -> bool:
    text = str(value)
    if _EMAIL_RE.search(text) or _SSN_RE.search(text) or _IBAN_RE.search(text):
        return True
    for match in _PAN_CANDIDATE_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return True
    return False


def prepare_evidence_rows(
    rows: Sequence[dict[str, Any]],
    *,
    sample_cap: int,
    redact: bool,
    patterns: Sequence[str],
    aggregate_only: bool = False,
) -> list[dict[str, Any]]:
    """Cap to sample_cap rows and, when redacting, replace any value whose
    column name matches a configured pattern or whose content looks like PII
    (card number via Luhn, SSN, email, IBAN)."""
    if aggregate_only:
        return []
    capped = [dict(row) for row in rows[:sample_cap]]
    if not redact:
        return capped
    compiled = [re.compile(p) for p in patterns]
    return [
        {
            key: (
                REDACTED
                if any(c.search(str(key)) for c in compiled) or _looks_like_pii(value)
                else value
            )
            for key, value in row.items()
        }
        for row in capped
    ]


def evidence_rows_for(ruleset: Ruleset, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the ruleset's evidence policy. This is what checks call."""
    defaults = ruleset.defaults
    return prepare_evidence_rows(
        rows,
        sample_cap=defaults.evidence_sample_rows,
        redact=defaults.redact_pii,
        patterns=ruleset.pii_column_patterns,
        aggregate_only=defaults.aggregate_only,
    )

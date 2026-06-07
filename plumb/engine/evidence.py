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


def prepare_evidence_rows(
    rows: Sequence[dict[str, Any]],
    *,
    sample_cap: int,
    redact: bool,
    patterns: Sequence[str],
    aggregate_only: bool = False,
) -> list[dict[str, Any]]:
    """Cap to sample_cap rows and redact values in columns whose name
    matches any of the configured patterns."""
    if aggregate_only:
        return []
    capped = [dict(row) for row in rows[:sample_cap]]
    if not redact or not patterns:
        return capped
    compiled = [re.compile(p) for p in patterns]
    return [
        {
            key: (REDACTED if any(c.search(str(key)) for c in compiled) else value)
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

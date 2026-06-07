"""Shared helpers every check uses to emit a CheckResult.

A check function focuses on its logic and calls build_result, which fills
identity (name, family) from the registry, resolves the effective severity
from the ruleset's severity_overrides, and runs evidence rows through the
capping and PII redaction policy. This keeps each check small and keeps
the severity and evidence invariants in one place.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from plumb.config.models import Ruleset
from plumb.engine.evidence import evidence_rows_for
from plumb.engine.models import (
    CheckResult,
    Evidence,
    Severity,
    Status,
)
from plumb.engine.registry import CheckContext, get_check


def resolve_severity(ruleset: Ruleset | None, check_id: str, default: Severity) -> Severity:
    if ruleset is not None and check_id in ruleset.severity_overrides:
        return ruleset.severity_overrides[check_id]
    return default


def build_result(
    ctx: CheckContext,
    check_id: str,
    status: Status,
    *,
    observed: str | None = None,
    expected: str | None = None,
    query: str | None = None,
    evidence_rows: Sequence[dict[str, Any]] | None = None,
    remediation: str | None = None,
    duration_ms: int | None = None,
) -> CheckResult:
    definition = get_check(check_id)
    severity = resolve_severity(ctx.ruleset, check_id, definition.default_severity)
    rows: list[dict[str, Any]] = []
    if evidence_rows and isinstance(ctx.ruleset, Ruleset):
        rows = evidence_rows_for(ctx.ruleset, evidence_rows)
    return CheckResult(
        id=check_id,
        name=definition.name,
        family=definition.family,
        severity=severity,
        status=status,
        observed=observed,
        expected=expected,
        evidence=Evidence(query=query, sample_rows=rows),
        remediation=remediation,
        duration_ms=duration_ms,
    )


def skip(ctx: CheckContext, check_id: str, reason: str) -> CheckResult:
    """A check that did not run by design. The reason surfaces in coverage."""
    return build_result(ctx, check_id, Status.SKIP, observed=reason)


def error(ctx: CheckContext, check_id: str, message: str) -> CheckResult:
    """A check that failed to run. Surfaced separately, never a pass."""
    return build_result(ctx, check_id, Status.ERROR, observed=message)

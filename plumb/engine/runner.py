"""Run orchestration: ruleset in, RunResult out.

The runner is the one place that turns a configuration plus a target into
a verdict. It is deterministic and stateless: no shared mutable state
between runs, so CI can fan out horizontally. Every surface (CLI, the
Phase 2 web UI, AI assist) calls run_checks and consumes the RunResult;
none reimplements verdict or coverage logic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import plumb.checks  # noqa: F401 - populates the registry on import
from plumb.baseline.store import BaselineStore
from plumb.config.models import Ruleset
from plumb.engine.models import (
    CheckFamily,
    CheckResult,
    Environment,
    RunResult,
    Status,
    Target,
    utc_now,
)
from plumb.engine.registry import (
    CheckContext,
    UnknownCheckError,
    get_check,
)
from plumb.engine.verdict import compute_coverage, compute_summary, compute_verdict

# Which check families apply to each target type. The runner only runs
# checks whose family is applicable, so a SQL target never emits Tableau
# results and a Tableau target never emits SQL results. This keeps coverage
# honest: it reports only families relevant to what was actually checked.
_FAMILIES_FOR_TARGET: dict[str, set[CheckFamily]] = {
    "sql": {
        CheckFamily.STATIC,
        CheckFamily.METADATA,
        CheckFamily.ASSERTIONS,
        CheckFamily.REGRESSION,
        CheckFamily.PERFORMANCE,
    },
    "tableau": {CheckFamily.TABLEAU_STATIC, CheckFamily.TABLEAU_LIVE},
    "parity": {CheckFamily.MIGRATION_PARITY},
}


@dataclass
class RunRequest:
    target: Target
    ruleset: Ruleset
    sql_text: str | None = None
    profile: str | None = None
    session: Any | None = None
    baseline_store: BaselineStore | None = None
    baseline_name: str | None = None
    workbook: Any | None = None
    run_id: str | None = None
    extras: dict[str, Any] | None = None


def run_checks(request: RunRequest) -> RunResult:
    run_id = request.run_id or str(uuid.uuid4())
    ruleset = request.ruleset

    ctx = CheckContext(
        run_id=run_id,
        target=request.target,
        sql_text=request.sql_text,
        session=request.session,
        ruleset=ruleset,
        baseline_store=request.baseline_store,
        workbook=request.workbook,
        extras={"baseline_name": request.baseline_name, **(request.extras or {})},
    )

    applicable = _FAMILIES_FOR_TARGET.get(request.target.type, set())
    results: list[CheckResult] = []
    for spec in ruleset.checks:
        if not spec.enabled:
            continue
        try:
            definition = get_check(spec.id)
        except UnknownCheckError:
            # A ruleset can reference a check id not present in this build.
            # Skip it visibly rather than crash; the registry is the source
            # of truth for what this build can run.
            continue
        if definition.family not in applicable:
            # Not relevant to this target type (for example a SQL check on a
            # Tableau target). Do not emit a result; coverage stays focused.
            continue
        try:
            outcome = definition.fn(ctx, spec.params)
        except Exception as exc:  # noqa: BLE001 - a crashing check must become
            # an ERROR result, never a crashed run: ERROR is surfaced
            # separately, caps the verdict honestly (ADR-0001), and the other
            # checks still report. An unexpected driver row shape or a check
            # bug must not take down the whole verdict surface with it.
            outcome = CheckResult(
                id=definition.check_id,
                name=definition.name,
                family=definition.family,
                severity=definition.default_severity,
                status=Status.ERROR,
                observed=f"check crashed: {type(exc).__name__}: {exc}",
            )
        if isinstance(outcome, list):
            results.extend(outcome)
        else:
            results.append(outcome)

    verdict = compute_verdict(results)
    summary = compute_summary(results)
    coverage = compute_coverage(results)

    environment = Environment(
        warehouse=_session_attr(request.session, "profile", "warehouse"),
        role=_session_attr(request.session, "profile", "role"),
        query_tag=getattr(request.session, "query_tag", None),
    )

    return RunResult(
        run_id=run_id,
        timestamp=utc_now(),
        target=request.target,
        ruleset_version=ruleset.version,
        profile=request.profile,
        verdict=verdict,
        coverage=coverage,
        summary=summary,
        checks=results,
        environment=environment,
    )


def _session_attr(session: Any | None, *path: str) -> str | None:
    node: Any = session
    for part in path:
        if node is None:
            return None
        node = getattr(node, part, None)
    return node if isinstance(node, str) else None


__all__ = ["RunRequest", "run_checks", "Status"]

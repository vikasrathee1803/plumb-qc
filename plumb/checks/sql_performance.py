"""Stream D: performance and cost smells via EXPLAIN.

These are advisory (LOW, except cardinality MEDIUM). They run EXPLAIN
USING TABULAR on the target through the read-only session and read what
the plan exposes. When a signal genuinely needs the runtime query profile
(spillage), the check SKIPs with a clear reason rather than guessing, so
coverage stays honest. EXPLAIN field names vary by account, so each check
reads fields defensively and SKIPs if the needed field is absent.
"""

from __future__ import annotations

from typing import Any

from plumb.checks._base import build_result, error
from plumb.checks._sql import TARGET_CTE, SqlParseError, wrap_target
from plumb.engine.models import CheckFamily, ExecutionType, Severity, Status
from plumb.engine.registry import CheckContext, register_check


def _explain_rows(ctx: CheckContext) -> list[dict[str, Any]]:
    body = f"SELECT * FROM {TARGET_CTE}"
    explain_sql = "EXPLAIN USING TABULAR " + wrap_target(ctx.sql_text, body)
    return ctx.session.execute(explain_sql).rows


def _num(row: dict[str, Any], *names: str) -> float | None:
    for name in names:
        for key in row:
            if key.lower() == name.lower() and row[key] is not None:
                try:
                    return float(row[key])
                except (TypeError, ValueError):
                    return None
    return None


def _str(row: dict[str, Any], *names: str) -> str:
    for name in names:
        for key in row:
            if key.lower() == name.lower() and row[key] is not None:
                return str(row[key])
    return ""


def _guard(ctx: CheckContext, check_id: str):
    if not ctx.sql_text:
        return build_result(ctx, check_id, Status.SKIP, observed="no SQL provided")
    if ctx.session is None:
        return build_result(
            ctx, check_id, Status.SKIP, observed="no Snowflake session (static-only run)"
        )
    return None


@register_check(
    check_id="P-PROF-001",
    name="Plan analysis: full table scans and weak pruning",
    family=CheckFamily.PERFORMANCE,
    default_severity=Severity.LOW,
    execution_type=ExecutionType.EXECUTION,
)
def p_prof_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "P-PROF-001")
    if guard is not None:
        return guard
    min_partitions = int(params.get("min_partitions_to_flag", 100))
    try:
        rows = _explain_rows(ctx)
    except SqlParseError as exc:
        return error(ctx, "P-PROF-001", f"could not build EXPLAIN: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "P-PROF-001", f"EXPLAIN failed: {exc}")

    weak = []
    for row in rows:
        op = _str(row, "operation")
        total = _num(row, "partitionsTotal", "partitions_total")
        assigned = _num(row, "partitionsAssigned", "partitions_assigned")
        if total and assigned and total >= min_partitions and assigned >= total:
            weak.append(f"{op or 'scan'} reads all {int(total)} partitions")
    if not any(_num(r, "partitionsTotal", "partitions_total") for r in rows):
        return build_result(
            ctx, "P-PROF-001", Status.SKIP, observed="EXPLAIN exposed no partition data"
        )
    if weak:
        return build_result(
            ctx,
            "P-PROF-001",
            Status.WARN,
            observed="; ".join(weak),
            expected="pruning eliminates partitions",
            remediation="Add a selective filter on the clustering or partition key.",
        )
    return build_result(ctx, "P-PROF-001", Status.PASS, observed="no full-scan smell")


@register_check(
    check_id="P-COST-001",
    name="Estimated bytes or partitions scanned vs threshold",
    family=CheckFamily.PERFORMANCE,
    default_severity=Severity.LOW,
    execution_type=ExecutionType.EXECUTION,
)
def p_cost_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "P-COST-001")
    if guard is not None:
        return guard
    max_partitions = params.get("max_partitions")
    max_bytes = params.get("max_bytes")
    if max_partitions is None and max_bytes is None:
        return build_result(
            ctx, "P-COST-001", Status.SKIP, observed="no max_partitions or max_bytes threshold"
        )
    try:
        rows = _explain_rows(ctx)
    except SqlParseError as exc:
        return error(ctx, "P-COST-001", f"could not build EXPLAIN: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "P-COST-001", f"EXPLAIN failed: {exc}")

    total_partitions = sum(
        _num(r, "partitionsAssigned", "partitions_assigned") or 0 for r in rows
    )
    total_bytes = sum(_num(r, "bytesAssigned", "bytes_assigned") or 0 for r in rows)
    breaches = []
    if max_partitions is not None and total_partitions > float(max_partitions):
        breaches.append(f"{int(total_partitions)} partitions > {max_partitions}")
    if max_bytes is not None and total_bytes > float(max_bytes):
        breaches.append(f"{int(total_bytes)} bytes > {max_bytes}")
    if breaches:
        return build_result(
            ctx,
            "P-COST-001",
            Status.WARN,
            observed="; ".join(breaches),
            expected=f"within partitions<={max_partitions}, bytes<={max_bytes}",
            remediation="The scan estimate exceeds the budget; narrow the query.",
        )
    return build_result(ctx, "P-COST-001", Status.PASS, observed="scan estimate within budget")


@register_check(
    check_id="P-SPILL-001",
    name="Query profile shows spillage to local or remote disk",
    family=CheckFamily.PERFORMANCE,
    default_severity=Severity.LOW,
    execution_type=ExecutionType.EXECUTION,
)
def p_spill_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "P-SPILL-001")
    if guard is not None:
        return guard
    # Spillage is only visible in the runtime query profile, which requires
    # executing the query and reading QUERY_HISTORY. Plumb does not run the
    # analyst's full query for a LOW performance smell, so this is an honest
    # skip rather than a guess. It can be enabled in Phase 2 with profile access.
    return build_result(
        ctx,
        "P-SPILL-001",
        Status.SKIP,
        observed="needs runtime query profile (not collected for a LOW perf check)",
    )


@register_check(
    check_id="P-CARD-001",
    name="Intermediate cardinality explosion detected",
    family=CheckFamily.PERFORMANCE,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.EXECUTION,
)
def p_card_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "P-CARD-001")
    if guard is not None:
        return guard
    blow_up_factor = float(params.get("blow_up_factor", 10.0))
    try:
        rows = _explain_rows(ctx)
    except SqlParseError as exc:
        return error(ctx, "P-CARD-001", f"could not build EXPLAIN: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "P-CARD-001", f"EXPLAIN failed: {exc}")

    row_estimates: list[float] = []
    for r in rows:
        value = _num(r, "rows", "estimatedRows", "estimated_rows")
        if value is not None:
            row_estimates.append(value)
    if not row_estimates:
        return build_result(
            ctx, "P-CARD-001", Status.SKIP, observed="EXPLAIN exposed no row estimates"
        )
    positives = [r for r in row_estimates if r > 0]
    max_rows = max(row_estimates)
    min_rows = min(positives) if positives else 1.0
    if max_rows >= min_rows * blow_up_factor:
        return build_result(
            ctx,
            "P-CARD-001",
            Status.WARN,
            observed=f"intermediate rows expand from ~{int(min_rows)} to ~{int(max_rows)}",
            expected=f"no operator expands cardinality beyond {blow_up_factor}x",
            remediation="An intermediate join or unnest is exploding rows; check the grain.",
        )
    return build_result(ctx, "P-CARD-001", Status.PASS, observed="no cardinality explosion")

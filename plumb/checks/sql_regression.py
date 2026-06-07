"""Stream C: regression diff against a saved golden baseline.

The confidence centerpiece. R-DIFF-001 compares the current result set to
the baseline: schema drift, then row-level adds and removes, then an
aggregate tie-out within tolerance. R-AGG-001 is the cheaper aggregate
fingerprint signal for large result sets. With no baseline, both SKIP with
the reason "no baseline found", which surfaces in coverage so a green run
never hides a missing regression check.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from plumb.baseline.store import BaselineStore, compute_aggregates
from plumb.checks import _sql
from plumb.checks._base import build_result, error
from plumb.checks._sql import SqlParseError
from plumb.engine.models import CheckFamily, ExecutionType, Severity, Status
from plumb.engine.registry import CheckContext, register_check

NO_BASELINE = "no baseline found"


def _baseline_name(ctx: CheckContext, params: dict) -> str | None:
    return ctx.extras.get("baseline_name") or params.get("baseline")


def _load_baseline(ctx: CheckContext, params: dict):
    store: BaselineStore | None = ctx.baseline_store
    name = _baseline_name(ctx, params)
    if store is None or not name:
        return None, None
    return name, store.load(name)


def _row_key(row: dict[str, Any]) -> tuple:
    return tuple(sorted((str(k), str(v)) for k, v in row.items()))


def _current_rows(ctx: CheckContext, cap: int) -> tuple[list[dict[str, Any]], list[str], str]:
    query = _sql.select_all_query(ctx.sql_text, cap)
    result = ctx.session.execute(query)
    columns = list(result.rows[0].keys()) if result.rows else []
    return result.rows, columns, query


@register_check(
    check_id="R-DIFF-001",
    name="Result-set diff vs saved golden baseline",
    family=CheckFamily.REGRESSION,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.EXECUTION,
)
def r_diff_001(ctx: CheckContext, params: dict):
    if not ctx.sql_text:
        return build_result(ctx, "R-DIFF-001", Status.SKIP, observed="no SQL provided")
    name, baseline = _load_baseline(ctx, params)
    if baseline is None:
        return build_result(ctx, "R-DIFF-001", Status.SKIP, observed=NO_BASELINE)
    if ctx.session is None:
        return build_result(
            ctx, "R-DIFF-001", Status.SKIP, observed="no Snowflake session (static-only run)"
        )
    cap = int(params.get("max_compare_rows", 10000))
    tol_pct = float(params.get("tolerance_pct", 0.0))
    try:
        current_rows, current_cols, query = _current_rows(ctx, cap)
    except SqlParseError as exc:
        return error(ctx, "R-DIFF-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "R-DIFF-001", f"baseline comparison query failed: {exc}")

    added_cols = sorted(set(current_cols) - set(baseline.columns))
    removed_cols = sorted(set(baseline.columns) - set(current_cols))
    if added_cols or removed_cols:
        return build_result(
            ctx,
            "R-DIFF-001",
            Status.FAIL,
            observed=f"schema changed: added {added_cols}, removed {removed_cols}",
            expected=f"schema matches baseline {baseline.columns}",
            query=query,
            remediation="The output schema drifted from the baseline; confirm intended.",
        )

    base_counter = Counter(_row_key(r) for r in baseline.rows)
    cur_counter = Counter(_row_key(r) for r in current_rows)
    added = cur_counter - base_counter
    removed = base_counter - cur_counter
    n_added = sum(added.values())
    n_removed = sum(removed.values())

    current_aggs = compute_aggregates(current_cols, current_rows)
    agg_breaches = _aggregate_breaches(baseline.aggregates, current_aggs, tol_pct)

    if n_added or n_removed or agg_breaches:
        sample = _diff_sample(added, removed, current_rows, baseline.rows)
        detail = f"{n_added} rows added, {n_removed} rows removed"
        if agg_breaches:
            detail += f"; aggregate drift: {', '.join(agg_breaches)}"
        return build_result(
            ctx,
            "R-DIFF-001",
            Status.FAIL,
            observed=detail,
            expected="no change vs baseline within tolerance",
            query=query,
            evidence_rows=sample,
            remediation="Review the moved rows; if intended, update the baseline.",
        )
    return build_result(
        ctx,
        "R-DIFF-001",
        Status.PASS,
        observed=f"matches baseline {name} ({baseline.row_count} rows)",
        query=query,
    )


@register_check(
    check_id="R-AGG-001",
    name="Aggregate fingerprint diff",
    family=CheckFamily.REGRESSION,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.EXECUTION,
)
def r_agg_001(ctx: CheckContext, params: dict):
    if not ctx.sql_text:
        return build_result(ctx, "R-AGG-001", Status.SKIP, observed="no SQL provided")
    name, baseline = _load_baseline(ctx, params)
    if baseline is None:
        return build_result(ctx, "R-AGG-001", Status.SKIP, observed=NO_BASELINE)
    if ctx.session is None:
        return build_result(
            ctx, "R-AGG-001", Status.SKIP, observed="no Snowflake session (static-only run)"
        )
    cap = int(params.get("max_compare_rows", 100000))
    tol_pct = float(params.get("tolerance_pct", 0.0))
    try:
        current_rows, current_cols, query = _current_rows(ctx, cap)
    except SqlParseError as exc:
        return error(ctx, "R-AGG-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "R-AGG-001", f"aggregate query failed: {exc}")

    current_aggs = compute_aggregates(current_cols, current_rows)
    breaches = _aggregate_breaches(baseline.aggregates, current_aggs, tol_pct)
    if breaches:
        return build_result(
            ctx,
            "R-AGG-001",
            Status.FAIL,
            observed=f"aggregate fingerprint drift: {', '.join(breaches)}",
            expected="aggregates match baseline within tolerance",
            query=query,
            remediation="A summed measure or row count moved; investigate before publishing.",
        )
    return build_result(
        ctx, "R-AGG-001", Status.PASS, observed=f"aggregates match baseline {name}", query=query
    )


def _aggregate_breaches(
    baseline: dict[str, float], current: dict[str, float], tol_pct: float
) -> list[str]:
    breaches = []
    for key, base_val in baseline.items():
        cur_val = current.get(key)
        if cur_val is None:
            breaches.append(f"{key} missing")
            continue
        allowed = abs(base_val) * tol_pct
        if abs(cur_val - base_val) > allowed:
            breaches.append(f"{key} {base_val}->{cur_val}")
    return breaches


def _diff_sample(
    added: Counter, removed: Counter, current_rows: list, baseline_rows: list, cap: int = 20
) -> list[dict[str, Any]]:
    sample: list[dict[str, Any]] = []
    added_keys = set(added)
    removed_keys = set(removed)
    for row in current_rows:
        if _row_key(row) in added_keys:
            sample.append({"__plumb_change": "added", **row})
        if len(sample) >= cap:
            return sample
    for row in baseline_rows:
        if _row_key(row) in removed_keys:
            sample.append({"__plumb_change": "removed", **row})
        if len(sample) >= cap:
            return sample
    return sample

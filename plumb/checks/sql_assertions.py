"""Stream B: execution data assertions, read-only.

These are the checks Plumb exists for: grain, nulls, referential
integrity, domain, range, freshness, reconciliation, full-row duplicates,
and the non-additive measure guard. Each builds one read query through
_sql, runs it on the session, and decides its status deterministically
from the returned numbers. With no session, or without the params it
needs, a check SKIPs rather than guessing; the skip surfaces in coverage.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from plumb.checks import _sql
from plumb.checks._base import build_result, error
from plumb.checks._sql import SqlParseError, parse_one
from plumb.engine.models import CheckFamily, ExecutionType, Severity, Status
from plumb.engine.registry import CheckContext, register_check


def _guard(ctx: CheckContext, check_id: str):
    if not ctx.sql_text:
        return build_result(ctx, check_id, Status.SKIP, observed="no SQL provided")
    if ctx.session is None:
        return build_result(
            ctx, check_id, Status.SKIP, observed="no Snowflake session (static-only run)"
        )
    return None


def _scalar(rows: list[dict[str, Any]], column: str) -> Any:
    return rows[0][column] if rows else None


def _threshold(ctx: CheckContext, name: str, default: float) -> float:
    thresholds = getattr(ctx.ruleset, "thresholds", None)
    if thresholds is not None:
        return float(getattr(thresholds, name, default))
    return default


_UNSET = object()


def _build_columns(ctx: CheckContext) -> set[str] | None:
    """The build's output columns (uppercased), probed once per run and cached.
    None when they cannot be determined (e.g. a SELECT * the parser cannot
    expand, or the probe failed); callers then let the check run as before."""
    cached = ctx.extras.get("__build_columns", _UNSET)
    if cached is not _UNSET:
        return cached
    cols: set[str] | None = None
    try:
        probe = _sql.wrap_target(ctx.sql_text or "", f"SELECT * FROM {_sql.TARGET_CTE} WHERE 1 = 0")
        cols = {c.upper() for c in ctx.session.execute(probe).columns} or None
    except Exception:  # noqa: BLE001 - a failed probe must not block the check
        cols = None
    ctx.extras["__build_columns"] = cols
    return cols


def _require_columns(ctx: CheckContext, check_id: str, columns: list[str]):
    """A SKIP result when a configured column is not in the build, else None.
    This turns Snowflake 'invalid identifier' errors into a clear, actionable
    skip: the check is configured for columns this build does not produce (often
    a check set tailored to another schema)."""
    have = _build_columns(ctx)
    if not have:
        return None  # unknown output (SELECT *, probe failed): run as before
    missing = [c for c in columns if c and c.upper() not in have]
    if missing:
        sample = ", ".join(sorted(have)[:10])
        return build_result(
            ctx,
            check_id,
            Status.SKIP,
            observed=(
                f"not run: {missing} not in this build (build has: {sample}). "
                "Set this check's columns in Customize to match your build, "
                "or pick a check set that fits your schema."
            ),
        )
    return None


@register_check(
    check_id="D-GRAIN-001",
    name="Grain uniqueness on declared key",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.BLOCKER,
    execution_type=ExecutionType.EXECUTION,
)
def d_grain_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-GRAIN-001")
    if guard is not None:
        return guard
    keys = params.get("key") or []
    if not keys:
        return build_result(
            ctx, "D-GRAIN-001", Status.SKIP, observed="no key declared in params"
        )
    gate = _require_columns(ctx, "D-GRAIN-001", keys)
    if gate is not None:
        return gate
    try:
        query = _sql.grain_count_query(ctx.sql_text, keys)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-GRAIN-001", f"could not build grain query: {exc}")
    except Exception as exc:  # noqa: BLE001 - surface as ERROR
        return error(ctx, "D-GRAIN-001", f"grain query failed: {exc}")

    if result.rows:
        max_dup = max(int(r["__PLUMB_DUP_COUNT"]) for r in result.rows)
        return build_result(
            ctx,
            "D-GRAIN-001",
            Status.FAIL,
            observed=f"{len(result.rows)} duplicate key group(s), max duplication {max_dup}x",
            expected=f"0 duplicates on {keys}",
            query=query,
            evidence_rows=result.rows,
            remediation="A join is fanning out. Aggregate to grain or fix the join key.",
        )
    return build_result(
        ctx, "D-GRAIN-001", Status.PASS, observed=f"key {keys} is unique", query=query
    )


@register_check(
    check_id="D-GRAIN-002",
    name="Row count within expected bounds",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.EXECUTION,
)
def d_grain_002(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-GRAIN-002")
    if guard is not None:
        return guard
    min_rows = params.get("min_rows")
    max_rows = params.get("max_rows")
    if min_rows is None and max_rows is None:
        return build_result(
            ctx,
            "D-GRAIN-002",
            Status.SKIP,
            observed="no min_rows or max_rows declared (baseline tolerance is R-DIFF)",
        )
    try:
        query = _sql.row_count_query(ctx.sql_text)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-GRAIN-002", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-GRAIN-002", f"row count query failed: {exc}")

    rows = int(_scalar(result.rows, "__PLUMB_ROWS") or 0)
    low_bad = min_rows is not None and rows < int(min_rows)
    high_bad = max_rows is not None and rows > int(max_rows)
    if low_bad or high_bad:
        return build_result(
            ctx,
            "D-GRAIN-002",
            Status.FAIL,
            observed=f"{rows} rows",
            expected=f"between {min_rows} and {max_rows}",
            query=query,
            remediation="Row count is outside the configured bounds; investigate filters or joins.",
        )
    return build_result(
        ctx, "D-GRAIN-002", Status.PASS, observed=f"{rows} rows within bounds", query=query
    )


@register_check(
    check_id="D-NULL-001",
    name="Declared key columns are not null",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.BLOCKER,
    execution_type=ExecutionType.EXECUTION,
)
def d_null_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-NULL-001")
    if guard is not None:
        return guard
    keys = params.get("key") or []
    if not keys:
        return build_result(
            ctx, "D-NULL-001", Status.SKIP, observed="no key declared in params"
        )
    gate = _require_columns(ctx, "D-NULL-001", keys)
    if gate is not None:
        return gate
    try:
        query = _sql.null_count_query(ctx.sql_text, keys)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-NULL-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-NULL-001", f"null count query failed: {exc}")

    offenders = {
        key: int(result.rows[0][_sql._null_alias(key)])
        for key in keys
        if result.rows and int(result.rows[0][_sql._null_alias(key)]) > 0
    }
    if offenders:
        detail = ", ".join(f"{k}={v}" for k, v in offenders.items())
        return build_result(
            ctx,
            "D-NULL-001",
            Status.FAIL,
            observed=f"null values in key column(s): {detail}",
            expected="0 nulls in key columns",
            query=query,
            remediation="A key column is null; the grain is broken upstream.",
        )
    return build_result(
        ctx, "D-NULL-001", Status.PASS, observed=f"no nulls in {keys}", query=query
    )


@register_check(
    check_id="D-NULL-002",
    name="Null rate within threshold",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.EXECUTION,
)
def d_null_002(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-NULL-002")
    if guard is not None:
        return guard
    columns = params.get("columns") or []
    if not columns:
        return build_result(
            ctx, "D-NULL-002", Status.SKIP, observed="no columns declared in params"
        )
    gate = _require_columns(ctx, "D-NULL-002", columns)
    if gate is not None:
        return gate
    threshold = float(params.get("threshold", _threshold(ctx, "null_rate_default", 0.0)))
    try:
        query = _sql.null_count_query(ctx.sql_text, columns)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-NULL-002", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-NULL-002", f"null count query failed: {exc}")

    total = int(_scalar(result.rows, "__PLUMB_TOTAL") or 0)
    if total == 0:
        return build_result(
            ctx, "D-NULL-002", Status.WARN, observed="target is empty", query=query
        )
    breaches = {}
    for col in columns:
        nulls = int(result.rows[0][_sql._null_alias(col)])
        rate = nulls / total
        if rate > threshold:
            breaches[col] = rate
    if breaches:
        detail = ", ".join(f"{k}={v:.4f}" for k, v in breaches.items())
        return build_result(
            ctx,
            "D-NULL-002",
            Status.FAIL,
            observed=f"null rate above {threshold}: {detail}",
            expected=f"null rate <= {threshold}",
            query=query,
            remediation="Investigate the source of nulls in these columns.",
        )
    return build_result(
        ctx, "D-NULL-002", Status.PASS, observed=f"null rates within {threshold}", query=query
    )


@register_check(
    check_id="D-BLANK-001",
    name="Blank (empty or whitespace) string rate within threshold",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.EXECUTION,
)
def d_blank_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-BLANK-001")
    if guard is not None:
        return guard
    columns = params.get("columns") or []
    if not columns:
        return build_result(
            ctx, "D-BLANK-001", Status.SKIP, observed="no columns declared in params"
        )
    gate = _require_columns(ctx, "D-BLANK-001", columns)
    if gate is not None:
        return gate
    threshold = float(params.get("threshold", 0.0))
    try:
        query = _sql.blank_count_query(ctx.sql_text, columns)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-BLANK-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-BLANK-001", f"blank count query failed: {exc}")

    total = int(_scalar(result.rows, "__PLUMB_TOTAL") or 0)
    if total == 0:
        return build_result(
            ctx, "D-BLANK-001", Status.WARN, observed="target is empty", query=query
        )
    breaches = {}
    for col in columns:
        blanks = int(result.rows[0][_sql._blank_alias(col)])
        rate = blanks / total
        if rate > threshold:
            breaches[col] = rate
    if breaches:
        detail = ", ".join(f"{k}={v:.4f}" for k, v in breaches.items())
        return build_result(
            ctx,
            "D-BLANK-001",
            Status.FAIL,
            observed=f"blank rate above {threshold}: {detail}",
            expected=f"blank rate <= {threshold}",
            query=query,
            remediation="Empty strings often masquerade as data; trim and validate at the source.",
        )
    return build_result(
        ctx, "D-BLANK-001", Status.PASS, observed=f"blank rates within {threshold}", query=query
    )


@register_check(
    check_id="D-POS-001",
    name="Declared numeric columns are non-negative",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.EXECUTION,
)
def d_pos_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-POS-001")
    if guard is not None:
        return guard
    columns = params.get("columns") or []
    if not columns:
        return build_result(
            ctx, "D-POS-001", Status.SKIP, observed="no columns declared in params"
        )
    gate = _require_columns(ctx, "D-POS-001", columns)
    if gate is not None:
        return gate
    try:
        query = _sql.negative_count_query(ctx.sql_text, columns)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-POS-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-POS-001", f"negative count query failed: {exc}")

    offenders = {
        col: int(result.rows[0][_sql._neg_alias(col)])
        for col in columns
        if result.rows and int(result.rows[0][_sql._neg_alias(col)]) > 0
    }
    if offenders:
        detail = ", ".join(f"{k}={v}" for k, v in offenders.items())
        return build_result(
            ctx,
            "D-POS-001",
            Status.FAIL,
            observed=f"negative values found: {detail}",
            expected="no negative values in these columns",
            query=query,
            remediation="Negative amounts or counts usually signal a sign error or bad join.",
        )
    return build_result(
        ctx, "D-POS-001", Status.PASS, observed=f"no negatives in {columns}", query=query
    )


@register_check(
    check_id="D-DISTINCT-001",
    name="Distinct value count within expected bounds",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.EXECUTION,
)
def d_distinct_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-DISTINCT-001")
    if guard is not None:
        return guard
    column = params.get("column")
    low = params.get("min")
    high = params.get("max")
    if not column or (low is None and high is None):
        return build_result(
            ctx, "D-DISTINCT-001", Status.SKIP, observed="needs column and min and/or max"
        )
    gate = _require_columns(ctx, "D-DISTINCT-001", [column])
    if gate is not None:
        return gate
    try:
        query = _sql.distinct_count_query(ctx.sql_text, column)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-DISTINCT-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-DISTINCT-001", f"distinct count query failed: {exc}")

    distinct = int(_scalar(result.rows, "__PLUMB_DISTINCT") or 0)
    low_bad = low is not None and distinct < int(low)
    high_bad = high is not None and distinct > int(high)
    if low_bad or high_bad:
        return build_result(
            ctx,
            "D-DISTINCT-001",
            Status.FAIL,
            observed=f"{distinct} distinct values in {column}",
            expected=f"between {low} and {high}",
            query=query,
            remediation="Distinct cardinality is off; check for a grain change or a bad filter.",
        )
    return build_result(
        ctx,
        "D-DISTINCT-001",
        Status.PASS,
        observed=f"{distinct} distinct values in {column}",
        query=query,
    )


@register_check(
    check_id="D-RI-001",
    name="Referential integrity: no orphan foreign keys",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.EXECUTION,
)
def d_ri_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-RI-001")
    if guard is not None:
        return guard
    fk = params.get("fk_column")
    ref_table = params.get("ref_table")
    ref_column = params.get("ref_column")
    if not (fk and ref_table and ref_column):
        return build_result(
            ctx,
            "D-RI-001",
            Status.SKIP,
            observed="needs fk_column, ref_table, ref_column in params",
        )
    gate = _require_columns(ctx, "D-RI-001", [fk])
    if gate is not None:
        return gate
    try:
        query = _sql.orphan_query(ctx.sql_text, fk, ref_table, ref_column)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-RI-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-RI-001", f"orphan query failed: {exc}")

    orphans = int(_scalar(result.rows, "__PLUMB_ORPHANS") or 0)
    if orphans:
        return build_result(
            ctx,
            "D-RI-001",
            Status.FAIL,
            observed=f"{orphans} orphan row(s) on {fk}",
            expected=f"every {fk} exists in {ref_table}.{ref_column}",
            query=query,
            remediation="Foreign keys point at rows that do not exist in the parent.",
        )
    return build_result(
        ctx, "D-RI-001", Status.PASS, observed=f"no orphans on {fk}", query=query
    )


@register_check(
    check_id="D-DOMAIN-001",
    name="Values fall within an allowed set",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.EXECUTION,
)
def d_domain_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-DOMAIN-001")
    if guard is not None:
        return guard
    column = params.get("column")
    allowed = params.get("allowed")
    if not column or allowed is None:
        return build_result(
            ctx, "D-DOMAIN-001", Status.SKIP, observed="needs column and allowed in params"
        )
    gate = _require_columns(ctx, "D-DOMAIN-001", [column])
    if gate is not None:
        return gate
    try:
        query = _sql.domain_violation_query(ctx.sql_text, column, list(allowed))
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-DOMAIN-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-DOMAIN-001", f"domain query failed: {exc}")

    violations = int(_scalar(result.rows, "__PLUMB_VIOLATIONS") or 0)
    if violations:
        return build_result(
            ctx,
            "D-DOMAIN-001",
            Status.FAIL,
            observed=f"{violations} value(s) outside the allowed set",
            expected=f"{column} in {list(allowed)}",
            query=query,
            remediation="Unexpected category values are present; check the source mapping.",
        )
    return build_result(
        ctx, "D-DOMAIN-001", Status.PASS, observed=f"{column} within allowed set", query=query
    )


@register_check(
    check_id="D-RANGE-001",
    name="Numeric or date values within expected bounds",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.EXECUTION,
)
def d_range_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-RANGE-001")
    if guard is not None:
        return guard
    column = params.get("column")
    low = params.get("min")
    high = params.get("max")
    if not column or (low is None and high is None):
        return build_result(
            ctx, "D-RANGE-001", Status.SKIP, observed="needs column and min and/or max"
        )
    gate = _require_columns(ctx, "D-RANGE-001", [column])
    if gate is not None:
        return gate
    try:
        query = _sql.range_violation_query(ctx.sql_text, column, low, high)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-RANGE-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-RANGE-001", f"range query failed: {exc}")

    violations = int(_scalar(result.rows, "__PLUMB_VIOLATIONS") or 0)
    if violations:
        return build_result(
            ctx,
            "D-RANGE-001",
            Status.FAIL,
            observed=f"{violations} value(s) out of range",
            expected=f"{column} between {low} and {high}",
            query=query,
            remediation="Out-of-range values often signal a unit or sign error.",
        )
    return build_result(
        ctx, "D-RANGE-001", Status.PASS, observed=f"{column} within range", query=query
    )


@register_check(
    check_id="D-FRESH-001",
    name="Freshness: max event timestamp within SLA",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.EXECUTION,
)
def d_fresh_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-FRESH-001")
    if guard is not None:
        return guard
    ts_col = params.get("event_ts_col")
    if not ts_col:
        return build_result(
            ctx, "D-FRESH-001", Status.SKIP, observed="no event_ts_col declared in params"
        )
    gate = _require_columns(ctx, "D-FRESH-001", [ts_col])
    if gate is not None:
        return gate
    sla_hours = float(params.get("sla_hours", _threshold(ctx, "freshness_sla_hours_default", 24.0)))
    try:
        query = _sql.freshness_query(ctx.sql_text, ts_col)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-FRESH-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-FRESH-001", f"freshness query failed: {exc}")

    if not result.rows or result.rows[0].get("__PLUMB_MAX_TS") is None:
        return build_result(
            ctx, "D-FRESH-001", Status.WARN, observed="no timestamp data found", query=query
        )
    max_ts = result.rows[0]["__PLUMB_MAX_TS"]
    now = result.rows[0]["__PLUMB_NOW"]
    age_hours = _hours_between(max_ts, now)
    if age_hours is None:
        return build_result(
            ctx, "D-FRESH-001", Status.WARN, observed=f"max ts {max_ts} (age not computable)",
            query=query,
        )
    if age_hours > sla_hours:
        return build_result(
            ctx,
            "D-FRESH-001",
            Status.FAIL,
            observed=f"data is {age_hours:.1f}h old",
            expected=f"<= {sla_hours}h old",
            query=query,
            remediation="The build is stale against its SLA; check the upstream load.",
        )
    return build_result(
        ctx, "D-FRESH-001", Status.PASS, observed=f"data is {age_hours:.1f}h old", query=query
    )


@register_check(
    check_id="D-RECON-001",
    name="Aggregates tie to a source of truth within tolerance",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.BLOCKER,
    execution_type=ExecutionType.EXECUTION,
)
def d_recon_001(ctx: CheckContext, params: dict):
    if ctx.session is None:
        return build_result(
            ctx, "D-RECON-001", Status.SKIP, observed="no Snowflake session (static-only run)"
        )
    metric_sql = params.get("metric_sql")
    truth_sql = params.get("source_of_truth_sql")
    if not metric_sql or not truth_sql:
        return build_result(
            ctx,
            "D-RECON-001",
            Status.SKIP,
            observed="needs metric_sql and source_of_truth_sql in params",
        )
    tol_abs = float(params.get("tolerance_abs", 0))
    tol_pct = float(params.get("tolerance_pct", 0))
    try:
        rendered_metric = (
            _sql.render_target_template(metric_sql, ctx.sql_text)
            if ctx.sql_text
            else metric_sql
        )
        metric_val = _to_number(_first_value(ctx.session.execute(rendered_metric).rows))
        truth_val = _to_number(_first_value(ctx.session.execute(truth_sql).rows))
    except SqlParseError as exc:
        return error(ctx, "D-RECON-001", f"could not build recon query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-RECON-001", f"recon query failed: {exc}")

    if metric_val is None or truth_val is None:
        return build_result(
            ctx, "D-RECON-001", Status.ERROR,
            observed="a recon query returned no scalar value",
        )
    diff = abs(metric_val - truth_val)
    allowed = max(tol_abs, abs(truth_val) * tol_pct)
    if diff > allowed:
        return build_result(
            ctx,
            "D-RECON-001",
            Status.FAIL,
            observed=f"metric {metric_val} vs source {truth_val}, difference {diff}",
            expected=f"difference <= {allowed} (abs {tol_abs}, pct {tol_pct})",
            remediation="The build does not tie to the source of truth; reconcile first.",
        )
    return build_result(
        ctx,
        "D-RECON-001",
        Status.PASS,
        observed=f"metric {metric_val} ties to source {truth_val} (diff {diff})",
    )


@register_check(
    check_id="D-DUP-001",
    name="Full-row duplicate detection",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.EXECUTION,
)
def d_dup_001(ctx: CheckContext, params: dict):
    guard = _guard(ctx, "D-DUP-001")
    if guard is not None:
        return guard
    try:
        query = _sql.full_dup_query(ctx.sql_text)
        result = ctx.session.execute(query)
    except SqlParseError as exc:
        return error(ctx, "D-DUP-001", f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001
        return error(ctx, "D-DUP-001", f"duplicate query failed: {exc}")

    dup_rows = int(_scalar(result.rows, "__PLUMB_DUP_ROWS") or 0)
    if dup_rows:
        return build_result(
            ctx,
            "D-DUP-001",
            Status.FAIL,
            observed=f"{dup_rows} fully duplicated row group(s)",
            expected="0 full-row duplicates",
            query=query,
            remediation="Identical rows repeat; add a key or a DISTINCT at the right grain.",
        )
    return build_result(
        ctx, "D-DUP-001", Status.PASS, observed="no full-row duplicates", query=query
    )


@register_check(
    check_id="D-ADD-001",
    name="Non-additive measure guard",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.EXECUTION,
)
def d_add_001(ctx: CheckContext, params: dict):
    """Static heuristic: a SUM wrapped directly around a division or an
    average is summing a ratio, which is almost never additive. WARN, since
    it needs human judgment."""
    if not ctx.sql_text:
        return build_result(ctx, "D-ADD-001", Status.SKIP, observed="no SQL provided")
    try:
        tree = parse_one(ctx.sql_text)
    except SqlParseError as exc:
        return error(ctx, "D-ADD-001", f"could not parse SQL: {exc}")
    flagged = 0
    for sum_node in tree.find_all(exp.Sum):
        inner = sum_node.this
        if isinstance(inner, exp.Div) or (
            isinstance(inner, exp.Avg)
        ):
            flagged += 1
        elif isinstance(inner, exp.Paren) and isinstance(inner.this, exp.Div):
            flagged += 1
    if flagged:
        return build_result(
            ctx,
            "D-ADD-001",
            Status.WARN,
            observed=f"{flagged} SUM over a ratio or average",
            expected="aggregate additive base measures, compute ratios last",
            remediation="Summing a ratio double counts; sum the parts, then divide.",
        )
    return build_result(
        ctx, "D-ADD-001", Status.PASS, observed="no non-additive measure pattern"
    )


def _first_value(rows: list[dict[str, Any]]) -> Any:
    if not rows:
        return None
    return next(iter(rows[0].values()), None)


def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hours_between(then: Any, now: Any) -> float | None:
    """Age in hours, robust to the types Snowflake returns. A DATE column
    comes back as datetime.date, a TIMESTAMP as datetime.datetime (often
    tz-aware). Coerce dates to midnight and normalize both to naive UTC so
    a DATE freshness column does not silently degrade to a WARN."""
    from datetime import date, datetime, timezone

    def _to_dt(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
        return None

    start = _to_dt(then)
    end = _to_dt(now)
    if start is None or end is None:
        return None
    if start.tzinfo is not None:
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
    if end.tzinfo is not None:
        end = end.astimezone(timezone.utc).replace(tzinfo=None)
    return (end - start).total_seconds() / 3600.0

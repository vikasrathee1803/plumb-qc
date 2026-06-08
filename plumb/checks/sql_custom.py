"""User-authored custom assertions (add your own check in the app).

A custom check is a SQL query that returns rows which must not exist. If it
returns any rows, the check FAILs and the offending rows become capped,
PII-redacted evidence. The query may use {{ target }} to reference the
build under test. Multiple custom checks coexist: each ruleset spec uses
the registered id D-CUSTOM-001 but carries its own name and SQL, and the
result is given a unique id (D-CUSTOM:<name>) so the UI and coverage keep
them distinct. The read-only guard still applies, so a non-read is refused.
"""

from __future__ import annotations

from typing import Any

from plumb.checks import _sql
from plumb.checks._sql import SqlParseError
from plumb.config.models import Ruleset
from plumb.engine.evidence import evidence_rows_for
from plumb.engine.models import (
    CheckFamily,
    CheckResult,
    Evidence,
    ExecutionType,
    Severity,
    Status,
)
from plumb.engine.registry import CheckContext, register_check


@register_check(
    check_id="D-CUSTOM-001",
    name="Custom assertion",
    family=CheckFamily.ASSERTIONS,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.EXECUTION,
)
def d_custom_001(ctx: CheckContext, params: dict[str, Any]) -> CheckResult:
    name = (params.get("name") or "custom assertion").strip()
    cid = f"D-CUSTOM:{name}"
    raw_sql = params.get("sql")
    severity = _severity(params.get("severity"))

    def result(
        status: Status,
        *,
        observed: str | None = None,
        expected: str | None = None,
        query: str | None = None,
        rows: list[dict[str, Any]] | None = None,
        remediation: str | None = None,
    ) -> CheckResult:
        ev_rows: list[dict[str, Any]] = []
        if rows and isinstance(ctx.ruleset, Ruleset):
            ev_rows = evidence_rows_for(ctx.ruleset, rows)
        return CheckResult(
            id=cid,
            name=name,
            family=CheckFamily.ASSERTIONS,
            severity=severity,
            status=status,
            observed=observed,
            expected=expected,
            evidence=Evidence(query=query, sample_rows=ev_rows),
            remediation=remediation,
        )

    if not raw_sql or not str(raw_sql).strip():
        return result(Status.SKIP, observed="no SQL provided for this custom check")
    if ctx.session is None:
        return result(Status.SKIP, observed="no Snowflake session (static-only run)")

    try:
        if "{{" in raw_sql and ctx.sql_text:
            query = _sql.render_target_template(raw_sql, ctx.sql_text)
        else:
            query = raw_sql
        execution = ctx.session.execute(query)
    except SqlParseError as exc:
        return result(Status.ERROR, observed=f"could not build query: {exc}")
    except Exception as exc:  # noqa: BLE001 - surface as ERROR, never a pass
        return result(Status.ERROR, observed=f"custom query failed: {exc}")

    violations = len(execution.rows)
    if violations:
        return result(
            Status.FAIL,
            observed=f"{violations} row(s) violate this assertion",
            expected="0 violating rows",
            query=query,
            rows=execution.rows,
            remediation="The custom assertion returned rows; investigate or adjust it.",
        )
    return result(Status.PASS, observed="no violating rows", query=query)


def _severity(value: Any) -> Severity:
    if isinstance(value, str) and value.upper() in Severity.__members__:
        return Severity[value.upper()]
    return Severity.MEDIUM

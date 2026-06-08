"""Stream A: static SQL analysis with sqlglot and sqlfluff.

No execution. Policy on status (ADR-0008): a definitive structural fault
is a FAIL; a heuristic signal that needs human judgment is a WARN, which
per the verdict model is always a note and never escalates. Each check
parses the target once via _sql.parse_one; an unparseable target yields a
single ERROR so it is surfaced, never silently passed.
"""

from __future__ import annotations

from sqlfluff.core import FluffConfig, Linter
from sqlglot import exp

from plumb.checks._base import build_result, error
from plumb.checks._sql import SqlParseError, parse_one
from plumb.engine.models import CheckFamily, ExecutionType, Severity, Status
from plumb.engine.registry import CheckContext, register_check

_LINT_RULES = "AM04,AM05,ST05,CV09,RF01"


def _tree(ctx: CheckContext):
    if not ctx.sql_text:
        return None
    return parse_one(ctx.sql_text)


@register_check(
    check_id="S-LINT-001",
    name="Style and convention lint against org rules",
    family=CheckFamily.STATIC,
    default_severity=Severity.LOW,
    execution_type=ExecutionType.STATIC,
)
def s_lint_001(ctx: CheckContext, params: dict):
    if not ctx.sql_text:
        return build_result(ctx, "S-LINT-001", Status.SKIP, observed="no SQL provided")
    try:
        rules = params.get("rules", _LINT_RULES)
        cfg = FluffConfig(overrides={"dialect": "snowflake", "rules": rules})
        result = Linter(config=cfg).lint_string(ctx.sql_text)
    except Exception as exc:  # noqa: BLE001 - lint must never crash a run
        return error(ctx, "S-LINT-001", f"sqlfluff failed: {exc}")
    violations = result.violations
    if not violations:
        return build_result(ctx, "S-LINT-001", Status.PASS, observed="no lint violations")
    rows = [
        {"rule": v.rule_code(), "line": v.line_no, "description": v.description}
        for v in violations[:20]
    ]
    return build_result(
        ctx,
        "S-LINT-001",
        Status.WARN,
        observed=f"{len(violations)} style violations",
        expected="0 style violations",
        evidence_rows=rows,
        remediation="Run the org formatter or address the listed rules.",
    )


@register_check(
    check_id="S-STAT-001",
    name="SELECT * in a production query",
    family=CheckFamily.STATIC,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.STATIC,
)
def s_stat_001(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-001", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-001", Status.SKIP, observed="no SQL provided")
    stars = [
        s for s in tree.find_all(exp.Star)
        if not isinstance(s.parent, exp.Count)
    ]
    if stars:
        return build_result(
            ctx,
            "S-STAT-001",
            Status.FAIL,
            observed=f"{len(stars)} SELECT * projection(s)",
            expected="explicit column list",
            remediation="List the columns explicitly so the contract is stable.",
        )
    return build_result(ctx, "S-STAT-001", Status.PASS, observed="no SELECT *")


@register_check(
    check_id="S-STAT-002",
    name="Cross or cartesian join with no join condition",
    family=CheckFamily.STATIC,
    default_severity=Severity.BLOCKER,
    execution_type=ExecutionType.STATIC,
)
def s_stat_002(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-002", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-002", Status.SKIP, observed="no SQL provided")

    offenders = 0
    # Explicit JOIN with no ON/USING and not a natural/cross-by-design join.
    for join in tree.find_all(exp.Join):
        kind = (join.kind or "").upper()
        if kind == "CROSS":
            continue
        if join.args.get("on") is None and not join.args.get("using"):
            if "NATURAL" in (join.method or "").upper():
                continue
            offenders += 1
    # Comma joins: more than one table in a FROM with no WHERE linking them
    # is the classic accidental cartesian product.
    for select in tree.find_all(exp.Select):
        from_ = select.args.get("from")
        if from_ is None:
            continue
        comma_tables = [
            j for j in select.args.get("joins", []) if not j.args.get("on")
            and not j.args.get("using") and not j.kind and not j.side
        ]
        if comma_tables and select.args.get("where") is None:
            offenders += len(comma_tables)

    if offenders:
        return build_result(
            ctx,
            "S-STAT-002",
            Status.FAIL,
            observed=f"{offenders} join(s) with no join condition",
            expected="every join has an ON or USING condition",
            remediation="Add the join key, or use explicit CROSS JOIN if intended.",
        )
    return build_result(ctx, "S-STAT-002", Status.PASS, observed="all joins have conditions")


@register_check(
    check_id="S-STAT-003",
    name="NOT IN with a subquery (NULL trap)",
    family=CheckFamily.STATIC,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.STATIC,
)
def s_stat_003(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-003", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-003", Status.SKIP, observed="no SQL provided")
    offenders = 0
    # Snowflake parses NOT IN (subquery) as <> ALL(subquery): an All node
    # under a NEQ wrapping a Select. This is the NULL trap.
    for all_node in tree.find_all(exp.All):
        if isinstance(all_node.parent, exp.NEQ) and all_node.find(exp.Select) is not None:
            offenders += 1
    # Other dialect shapes: NOT wrapping an IN with a subquery.
    for not_node in tree.find_all(exp.Not):
        inner = not_node.this
        if isinstance(inner, exp.In) and (
            inner.args.get("query") is not None
            or isinstance(inner.args.get("field"), exp.Select)
        ):
            offenders += 1
    if offenders:
        return build_result(
            ctx,
            "S-STAT-003",
            Status.FAIL,
            observed=f"{offenders} NOT IN (subquery) predicate(s)",
            expected="NOT EXISTS instead of NOT IN with a subquery",
            remediation="Use NOT EXISTS; NOT IN returns no rows if the subquery has a NULL.",
        )
    return build_result(ctx, "S-STAT-003", Status.PASS, observed="no NOT IN subquery")


@register_check(
    check_id="S-STAT-004",
    name="Implicit type cast inside a join or filter predicate",
    family=CheckFamily.STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def s_stat_004(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-004", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-004", Status.SKIP, observed="no SQL provided")
    predicates: list[exp.Expression] = []
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None:
            predicates.append(on)
    for where in tree.find_all(exp.Where):
        predicates.append(where.this)

    flagged = 0
    for predicate in predicates:
        for cmp_node in predicate.find_all(exp.EQ, exp.NEQ, exp.GT, exp.LT, exp.GTE, exp.LTE):
            left, right = cmp_node.left, cmp_node.right
            if _is_column(left) and _is_string_literal(right):
                flagged += 1
            elif _is_column(right) and _is_string_literal(left):
                flagged += 1
            elif isinstance(left, exp.Cast) or isinstance(right, exp.Cast):
                flagged += 1
    if flagged:
        return build_result(
            ctx,
            "S-STAT-004",
            Status.WARN,
            observed=f"{flagged} predicate(s) with a possible implicit cast",
            expected="compare columns of matching types, cast explicitly",
            remediation="Confirm both sides share a type; an implicit cast can defeat pruning.",
        )
    return build_result(ctx, "S-STAT-004", Status.PASS, observed="no obvious implicit casts")


@register_check(
    check_id="S-STAT-005",
    name="Non-SARGable predicate (function wrapped around a filtered column)",
    family=CheckFamily.STATIC,
    default_severity=Severity.LOW,
    execution_type=ExecutionType.STATIC,
)
def s_stat_005(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-005", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-005", Status.SKIP, observed="no SQL provided")
    flagged = 0
    for where in tree.find_all(exp.Where):
        for cmp_node in where.find_all(exp.EQ, exp.NEQ, exp.GT, exp.LT, exp.GTE, exp.LTE):
            for side in (cmp_node.left, cmp_node.right):
                if isinstance(side, exp.Func) and any(
                    isinstance(c, exp.Column) for c in side.find_all(exp.Column)
                ):
                    flagged += 1
                    break
    if flagged:
        return build_result(
            ctx,
            "S-STAT-005",
            Status.WARN,
            observed=f"{flagged} non-SARGable predicate(s)",
            expected="filter on the bare column where possible",
            remediation="Wrapping a filtered column in a function prevents partition pruning.",
        )
    return build_result(ctx, "S-STAT-005", Status.PASS, observed="no non-SARGable predicates")


@register_check(
    check_id="S-STAT-006",
    name="Hardcoded literal, magic number, or hardcoded date",
    family=CheckFamily.STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def s_stat_006(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-006", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-006", Status.SKIP, observed="no SQL provided")
    date_like = 0
    for literal in tree.find_all(exp.Literal):
        if literal.is_string and _looks_like_date(literal.this):
            date_like += 1
    if date_like:
        return build_result(
            ctx,
            "S-STAT-006",
            Status.WARN,
            observed=f"{date_like} hardcoded date literal(s)",
            expected="parameterize dates or derive from a calendar",
            remediation="Hardcoded dates silently go stale; drive them from a parameter.",
        )
    return build_result(ctx, "S-STAT-006", Status.PASS, observed="no hardcoded dates")


@register_check(
    check_id="S-STAT-007",
    name="Reference to a deprecated or blocklisted object",
    family=CheckFamily.STATIC,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.STATIC,
)
def s_stat_007(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-007", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-007", Status.SKIP, observed="no SQL provided")
    deprecated = {d.upper() for d in (getattr(ctx.ruleset, "deprecated_objects", []) or [])}
    if not deprecated:
        return build_result(
            ctx, "S-STAT-007", Status.SKIP, observed="no deprecated objects configured"
        )
    from plumb.checks._sql import extract_table_refs

    hits = [
        r.fqn()
        for r in extract_table_refs(ctx.sql_text or "")
        if r.fqn().upper() in deprecated
    ]
    if hits:
        return build_result(
            ctx,
            "S-STAT-007",
            Status.FAIL,
            observed=f"references deprecated object(s): {', '.join(hits)}",
            expected="no references to deprecated or blocklisted objects",
            remediation="Repoint to the certified replacement source.",
        )
    return build_result(ctx, "S-STAT-007", Status.PASS, observed="no deprecated references")


@register_check(
    check_id="S-STAT-008",
    name="Ambiguous or implicit join type",
    family=CheckFamily.STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def s_stat_008(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-008", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-008", Status.SKIP, observed="no SQL provided")
    implicit = 0
    for select in tree.find_all(exp.Select):
        implicit += sum(
            1 for j in select.args.get("joins", [])
            if not j.kind and not j.side and not j.method and j.args.get("on")
        )
    if implicit:
        return build_result(
            ctx,
            "S-STAT-008",
            Status.WARN,
            observed=f"{implicit} join(s) with an implicit type",
            expected="state INNER or LEFT explicitly",
            remediation="Write the join type out so intent is unambiguous to reviewers.",
        )
    return build_result(ctx, "S-STAT-008", Status.PASS, observed="all join types explicit")


@register_check(
    check_id="S-STAT-010",
    name="DISTINCT used to mask a likely join fan-out",
    family=CheckFamily.STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def s_stat_010(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-010", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-010", Status.SKIP, observed="no SQL provided")
    flagged = 0
    for select in tree.find_all(exp.Select):
        if select.args.get("distinct") and select.args.get("joins"):
            flagged += 1
    if flagged:
        return build_result(
            ctx,
            "S-STAT-010",
            Status.WARN,
            observed=f"{flagged} SELECT DISTINCT over a join",
            expected="fix the grain rather than dedupe with DISTINCT",
            remediation="DISTINCT can hide a fan-out; confirm the join grain is correct.",
        )
    return build_result(ctx, "S-STAT-010", Status.PASS, observed="no DISTINCT-over-join")


@register_check(
    check_id="S-STAT-011",
    name="ORDER BY in a subquery or CTE (non-functional sort)",
    family=CheckFamily.STATIC,
    default_severity=Severity.LOW,
    execution_type=ExecutionType.STATIC,
)
def s_stat_011(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-011", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-011", Status.SKIP, observed="no SQL provided")
    flagged = 0
    for order in tree.find_all(exp.Order):
        # A sort inside a window (OVER ... ORDER BY) is functional; skip it.
        if order.find_ancestor(exp.Window) is not None:
            continue
        # A sort paired with LIMIT (top-N) inside a subquery is intentional.
        enclosing = order.find_ancestor(exp.Subquery, exp.CTE)
        if enclosing is None:
            continue
        parent_select = order.find_ancestor(exp.Select)
        if parent_select is not None and parent_select.args.get("limit") is not None:
            continue
        flagged += 1
    if flagged:
        return build_result(
            ctx,
            "S-STAT-011",
            Status.WARN,
            observed=f"{flagged} ORDER BY inside a subquery or CTE without LIMIT",
            expected="sort only in the outermost query",
            remediation="A subquery sort is discarded by the optimizer; sort in the final SELECT.",
        )
    return build_result(ctx, "S-STAT-011", Status.PASS, observed="no nested sorts")


def _is_column(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Column)


def _is_string_literal(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Literal) and bool(node.args.get("is_string"))


def _looks_like_date(value: str) -> bool:
    cleaned = value.strip()
    if len(cleaned) < 8:
        return False
    digits = sum(c.isdigit() for c in cleaned)
    seps = sum(c in "-/" for c in cleaned)
    return digits >= 6 and seps >= 2

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


def _short(text: object, limit: int = 120) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


_BIG_NODES = (exp.Select, exp.From, exp.Where, exp.Group, exp.Order, exp.Having, exp.With)


def _context_of(node: exp.Expression) -> str:
    """Render the predicate or expression around an offender so the report points
    at exactly where to look. For a leaf (a literal, column, or star) that is its
    enclosing predicate; never the whole clause."""
    target: exp.Expression = node
    if isinstance(node, (exp.Literal, exp.Column, exp.Star, exp.Null)) and node.parent is not None:
        target = node.parent
    if isinstance(target, _BIG_NODES):
        target = node
    return _short(target.sql(dialect="snowflake"))


def _detail(items: list[str], rows: list[dict[str, object]], noun: str) -> tuple[str, list[dict]]:
    """An observed string that names the first few offenders inline, plus the
    full evidence rows. So '4 hardcoded dates' becomes '4 hardcoded dates:
    o.dt = \\'2024-01-01\\'; ...' and the report lists each with its context."""
    preview = "; ".join(items[:3])
    observed = f"{len(items)} {noun}: {preview}" + (" …" if len(items) > 3 else "")
    return _short(observed, 220), rows[:25]


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
        items: list[str] = []
        rows: list[dict[str, object]] = []
        for s in stars:
            star_sql = s.sql(dialect="snowflake")
            sel = s.find_ancestor(exp.Select)
            tbls = (
                list(dict.fromkeys(t.sql(dialect="snowflake") for t in sel.find_all(exp.Table)))
                if sel is not None else []
            )
            src = ", ".join(tbls[:4])
            items.append(f"SELECT {star_sql}" + (f" FROM {src}" if src else ""))
            rows.append({"projection": star_sql, "from": src})
        observed, evidence = _detail(items, rows, "SELECT * projection(s)")
        return build_result(
            ctx,
            "S-STAT-001",
            Status.FAIL,
            observed=observed,
            expected="explicit column list",
            evidence_rows=evidence,
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

    offenders: list[str] = []
    rows: list[dict[str, object]] = []
    # Explicit JOIN with no ON/USING and not a natural/cross-by-design join.
    for join in tree.find_all(exp.Join):
        kind = (join.kind or "").upper()
        if kind == "CROSS":
            continue
        # Bare/comma joins (no kind, side, or method) are handled below, where
        # we also check the WHERE - an old-style 'FROM a, b WHERE a.id=b.id' is a
        # valid join, so flagging it here on the missing ON would be wrong.
        if not join.kind and not join.side and not join.method:
            continue
        if join.args.get("on") is None and not join.args.get("using"):
            if "NATURAL" in (join.method or "").upper():
                continue
            tname = join.this.sql(dialect="snowflake") if join.this is not None else "?"
            offenders.append(f"JOIN {tname} (no ON/USING)")
            rows.append({"table": tname, "issue": "JOIN with no ON/USING"})
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
            base = from_.this.sql(dialect="snowflake") if from_.this is not None else "?"
            for j in comma_tables:
                tname = j.this.sql(dialect="snowflake") if j.this is not None else "?"
                offenders.append(f"{base} , {tname} (comma join, no WHERE link)")
                rows.append({"tables": f"{base}, {tname}", "issue": "comma join, no WHERE link"})

    if offenders:
        observed, evidence = _detail(offenders, rows, "join(s) with no join condition")
        return build_result(
            ctx,
            "S-STAT-002",
            Status.FAIL,
            observed=observed,
            expected="every join has an ON or USING condition",
            evidence_rows=evidence,
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
    offenders: list[str] = []
    rows: list[dict[str, object]] = []
    # Snowflake parses NOT IN (subquery) as <> ALL(subquery): an All node
    # under a NEQ wrapping a Select. This is the NULL trap.
    for all_node in tree.find_all(exp.All):
        if (
            all_node.parent is not None
            and isinstance(all_node.parent, exp.NEQ)
            and all_node.find(exp.Select) is not None
        ):
            where = _short(all_node.parent.sql(dialect="snowflake"))
            offenders.append(where)
            rows.append({"predicate": where})
    # Other dialect shapes: NOT wrapping an IN with a subquery.
    for not_node in tree.find_all(exp.Not):
        inner = not_node.this
        if isinstance(inner, exp.In) and (
            inner.args.get("query") is not None
            or isinstance(inner.args.get("field"), exp.Select)
        ):
            where = _short(not_node.sql(dialect="snowflake"))
            offenders.append(where)
            rows.append({"predicate": where})
    if offenders:
        observed, evidence = _detail(offenders, rows, "NOT IN (subquery) predicate(s)")
        return build_result(
            ctx,
            "S-STAT-003",
            Status.FAIL,
            observed=observed,
            expected="NOT EXISTS instead of NOT IN with a subquery",
            evidence_rows=evidence,
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

    flagged: list[str] = []
    rows: list[dict[str, object]] = []
    for predicate in predicates:
        for cmp_node in predicate.find_all(exp.EQ, exp.NEQ, exp.GT, exp.LT, exp.GTE, exp.LTE):
            left, right = cmp_node.left, cmp_node.right
            hit = (
                (_is_column(left) and _is_string_literal(right))
                or (_is_column(right) and _is_string_literal(left))
                or isinstance(left, exp.Cast)
                or isinstance(right, exp.Cast)
            )
            if hit:
                where = _short(cmp_node.sql(dialect="snowflake"))
                flagged.append(where)
                rows.append({"predicate": where})
    if flagged:
        observed, evidence = _detail(flagged, rows, "predicate(s) with a possible implicit cast")
        return build_result(
            ctx,
            "S-STAT-004",
            Status.WARN,
            observed=observed,
            expected="compare columns of matching types, cast explicitly",
            evidence_rows=evidence,
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
    flagged: list[str] = []
    rows: list[dict[str, object]] = []
    for where in tree.find_all(exp.Where):
        for cmp_node in where.find_all(exp.EQ, exp.NEQ, exp.GT, exp.LT, exp.GTE, exp.LTE):
            for side in (cmp_node.left, cmp_node.right):
                if isinstance(side, exp.Func) and any(
                    isinstance(c, exp.Column) for c in side.find_all(exp.Column)
                ):
                    where_str = _short(cmp_node.sql(dialect="snowflake"))
                    flagged.append(where_str)
                    rows.append({"predicate": where_str, "wrapped": _short(side.sql("snowflake"))})
                    break
    if flagged:
        observed, evidence = _detail(flagged, rows, "non-SARGable predicate(s)")
        return build_result(
            ctx,
            "S-STAT-005",
            Status.WARN,
            observed=observed,
            expected="filter on the bare column where possible",
            evidence_rows=evidence,
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
    items: list[str] = []
    rows: list[dict[str, object]] = []
    for literal in tree.find_all(exp.Literal):
        if literal.is_string and _looks_like_date(literal.this):
            where = _context_of(literal)
            items.append(where)
            rows.append({"date": literal.sql(dialect="snowflake"), "in": where})
    if items:
        observed, evidence = _detail(items, rows, "hardcoded date literal(s)")
        return build_result(
            ctx,
            "S-STAT-006",
            Status.WARN,
            observed=observed,
            expected="parameterize dates or derive from a calendar",
            evidence_rows=evidence,
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


def _layer_hits(sql: str, patterns: list[str]) -> list[tuple[str, str]]:
    """Table refs whose database or schema carries a layer token, as
    (fully-qualified name, matched token). The layer is a database/schema
    convention (e.g. X_DEV_RL.SCH.T or RAW.ORDERS), so only those parts are
    matched - never the table name, where a token like DEV or RL would be a
    false positive. Matched token-wise (split on _) so DEVICE is not DEV."""
    from plumb.checks._sql import extract_table_refs

    pats = {p.upper() for p in patterns if p}
    if not pats:
        return []
    hits: list[tuple[str, str]] = []
    for ref in extract_table_refs(sql):
        tokens: set[str] = set()
        for part in (ref.catalog, ref.db):  # database and schema, not the table
            if not part:
                continue
            upper = part.upper()
            tokens.add(upper)
            tokens.update(t for t in upper.split("_") if t)
        matched = tokens & pats
        if matched:
            hits.append((ref.fqn(), sorted(matched)[0]))
    return hits


@register_check(
    check_id="S-STAT-012",
    name="Reads a sandbox, dev, or scratch object",
    family=CheckFamily.STATIC,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.STATIC,
)
def s_stat_012(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-012", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-012", Status.SKIP, observed="no SQL provided")
    patterns = params.get("patterns") or getattr(ctx.ruleset, "sandbox_patterns", []) or []
    if not patterns:
        return build_result(
            ctx, "S-STAT-012", Status.SKIP, observed="no sandbox patterns configured"
        )
    hits = _layer_hits(ctx.sql_text or "", patterns)
    if hits:
        names = ", ".join(f"{fqn} ({tok})" for fqn, tok in hits)
        return build_result(
            ctx,
            "S-STAT-012",
            Status.FAIL,
            observed=f"reads sandbox/dev object(s): {names}",
            expected="every source is a production object",
            remediation=(
                "A sandbox, dev, or scratch object will not exist in production, so the "
                "build breaks or returns different data. Repoint to the production equivalent."
            ),
        )
    return build_result(ctx, "S-STAT-012", Status.PASS, observed="no sandbox or dev references")


@register_check(
    check_id="S-STAT-013",
    name="Reads a raw-layer object directly",
    family=CheckFamily.STATIC,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.STATIC,
)
def s_stat_013(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-013", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-013", Status.SKIP, observed="no SQL provided")
    patterns = params.get("patterns") or getattr(ctx.ruleset, "raw_layer_patterns", []) or []
    if not patterns:
        return build_result(
            ctx, "S-STAT-013", Status.SKIP, observed="no raw-layer patterns configured"
        )
    hits = _layer_hits(ctx.sql_text or "", patterns)
    if hits:
        names = ", ".join(f"{fqn} ({tok})" for fqn, tok in hits)
        return build_result(
            ctx,
            "S-STAT-013",
            Status.FAIL,
            observed=f"reads raw-layer object(s) directly: {names}",
            expected="build on the modeled, certified layer (staging/core/mart)",
            remediation=(
                "Reading raw tables bypasses the tested layer: the grain, dedup, and business "
                "rules live downstream, so results can be wrong. Point at the modeled view/table."
            ),
        )
    return build_result(ctx, "S-STAT-013", Status.PASS, observed="no direct raw-layer references")


@register_check(
    check_id="S-STAT-015",
    name="Reads the integration layer instead of presentation",
    family=CheckFamily.STATIC,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.STATIC,
)
def s_stat_015(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-015", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-015", Status.SKIP, observed="no SQL provided")
    patterns = (
        params.get("patterns") or getattr(ctx.ruleset, "integration_layer_patterns", []) or []
    )
    if not patterns:
        return build_result(
            ctx, "S-STAT-015", Status.SKIP, observed="no integration-layer patterns configured"
        )
    hits = _layer_hits(ctx.sql_text or "", patterns)
    if hits:
        names = ", ".join(f"{fqn} ({tok})" for fqn, tok in hits)
        return build_result(
            ctx,
            "S-STAT-015",
            Status.FAIL,
            observed=f"reads the integration layer: {names}",
            expected="build on the presentation layer (the certified, analyst-facing layer)",
            remediation=(
                "The integration layer is modeled but not the final analyst-facing layer; "
                "its grain or business rules can still change. Point at the presentation view."
            ),
        )
    return build_result(ctx, "S-STAT-015", Status.PASS, observed="no integration-layer references")


@register_check(
    check_id="S-STAT-014",
    name="Outer join turned into an inner join by a WHERE filter",
    family=CheckFamily.STATIC,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.STATIC,
)
def s_stat_014(ctx: CheckContext, params: dict):
    try:
        tree = _tree(ctx)
    except SqlParseError as exc:
        return error(ctx, "S-STAT-014", f"could not parse SQL: {exc}")
    if tree is None:
        return build_result(ctx, "S-STAT-014", Status.SKIP, observed="no SQL provided")
    offenders: list[str] = []
    for select in tree.find_all(exp.Select):
        where = select.args.get("where")
        if where is None:
            continue
        for join in select.args.get("joins", []):
            side = (join.side or "").upper()
            if side not in ("LEFT", "FULL"):
                continue
            joined = join.this
            alias = (joined.alias_or_name or "").upper() if joined is not None else ""
            if not alias:
                continue
            filtered = [c for c in where.find_all(exp.Column) if (c.table or "").upper() == alias]
            if not filtered:
                continue
            # An explicit `alias.col IS NULL` means the analyst is handling the
            # outer side deliberately (anti-join / null-tolerant); do not flag.
            null_safe = any(
                isinstance(node.this, exp.Column)
                and (node.this.table or "").upper() == alias
                and isinstance(node.expression, exp.Null)
                for node in where.find_all(exp.Is)
            )
            if not null_safe:
                label = f"{joined.alias_or_name} ({side} JOIN)"
                if label not in offenders:
                    offenders.append(label)
    if offenders:
        return build_result(
            ctx,
            "S-STAT-014",
            Status.WARN,
            observed=f"WHERE filters the outer side of: {', '.join(offenders)}",
            expected="filter the outer table in the ON clause, or allow NULLs (OR col IS NULL)",
            remediation=(
                "A WHERE predicate on a LEFT/FULL-joined table drops the unmatched rows, "
                "silently making it an inner join - a common cause of missing rows and wrong "
                "totals. Move the condition into the JOIN ... ON, or add 'OR col IS NULL'."
            ),
        )
    return build_result(
        ctx, "S-STAT-014", Status.PASS, observed="no outer joins nullified by WHERE"
    )


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
    offenders: list[str] = []
    rows: list[dict[str, object]] = []
    for select in tree.find_all(exp.Select):
        for j in select.args.get("joins", []):
            if not j.kind and not j.side and not j.method and j.args.get("on"):
                tname = j.this.sql("snowflake") if j.this is not None else "?"
                offenders.append(f"JOIN {tname}")
                rows.append({"join": tname, "issue": "implicit join type (defaults to INNER)"})
    if offenders:
        observed, evidence = _detail(offenders, rows, "join(s) with an implicit type")
        return build_result(
            ctx,
            "S-STAT-008",
            Status.WARN,
            observed=observed,
            expected="state INNER or LEFT explicitly",
            evidence_rows=evidence,
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
    offenders: list[str] = []
    rows: list[dict[str, object]] = []
    for select in tree.find_all(exp.Select):
        if select.args.get("distinct") and select.args.get("joins"):
            tbls = list(dict.fromkeys(t.sql("snowflake") for t in select.find_all(exp.Table)))
            src = ", ".join(tbls[:4])
            offenders.append(f"SELECT DISTINCT over {src or 'a join'}")
            rows.append({"from": src, "issue": "DISTINCT over a join (may mask fan-out)"})
    if offenders:
        observed, evidence = _detail(offenders, rows, "SELECT DISTINCT over a join")
        return build_result(
            ctx,
            "S-STAT-010",
            Status.WARN,
            observed=observed,
            expected="fix the grain rather than dedupe with DISTINCT",
            evidence_rows=evidence,
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
    offenders: list[str] = []
    rows: list[dict[str, object]] = []
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
        name = enclosing.alias_or_name or "subquery"
        order_sql = _short(order.sql("snowflake"))
        offenders.append(f"{order_sql} in {name}")
        rows.append({"order_by": order_sql, "in": name})
    if offenders:
        observed, evidence = _detail(offenders, rows, "ORDER BY in a subquery/CTE without LIMIT")
        return build_result(
            ctx,
            "S-STAT-011",
            Status.WARN,
            observed=observed,
            expected="sort only in the outermost query",
            evidence_rows=evidence,
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

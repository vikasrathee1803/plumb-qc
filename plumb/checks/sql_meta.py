"""Stream A: schema and metadata checks against INFORMATION_SCHEMA.

These run read-only SELECTs against each referenced database's
INFORMATION_SCHEMA via the session. With no session (static-only run)
they SKIP, which surfaces honestly in coverage. Only database-qualified
references can be resolved; unqualified references are reported as
unverified, never assumed present.
"""

from __future__ import annotations

from sqlglot import exp

from plumb.checks._base import build_result, error
from plumb.checks._metadata import (
    columns_query,
    resolve_refs,
    tables_query,
    type_family,
)
from plumb.checks._sql import SqlParseError, parse_one
from plumb.engine.models import CheckFamily, ExecutionType, Severity, Status
from plumb.engine.registry import CheckContext, register_check


def _needs_session(ctx: CheckContext, check_id: str):
    if not ctx.sql_text:
        return build_result(ctx, check_id, Status.SKIP, observed="no SQL provided")
    if ctx.session is None:
        return build_result(
            ctx, check_id, Status.SKIP, observed="no Snowflake session (static-only run)"
        )
    return None


@register_check(
    check_id="S-META-001",
    name="All referenced tables exist",
    family=CheckFamily.METADATA,
    default_severity=Severity.BLOCKER,
    execution_type=ExecutionType.METADATA,
)
def s_meta_001(ctx: CheckContext, params: dict):
    skip = _needs_session(ctx, "S-META-001")
    if skip is not None:
        return skip
    try:
        refs = resolve_refs(ctx.sql_text or "")
    except SqlParseError as exc:
        return error(ctx, "S-META-001", f"could not parse SQL: {exc}")
    if not refs.qualified and not refs.unqualified:
        return build_result(ctx, "S-META-001", Status.SKIP, observed="no table references")

    missing: list[str] = []
    try:
        for database, db_refs in refs.by_database().items():
            result = ctx.session.execute(tables_query(database, db_refs))
            found = {
                f"{row['TABLE_SCHEMA']}.{row['TABLE_NAME']}".upper() for row in result.rows
            }
            for ref in db_refs:
                if f"{(ref.db or '').upper()}.{ref.name.upper()}" not in found:
                    missing.append(ref.fqn())
    except Exception as exc:  # noqa: BLE001 - surface as ERROR, never a pass
        return error(ctx, "S-META-001", f"metadata lookup failed: {exc}")

    if missing:
        return build_result(
            ctx,
            "S-META-001",
            Status.FAIL,
            observed=f"missing object(s): {', '.join(sorted(missing))}",
            expected="every referenced table and view exists",
            remediation="Fix the object name or build the missing upstream object.",
        )
    if refs.unqualified:
        return build_result(
            ctx,
            "S-META-001",
            Status.WARN,
            observed=(
                f"{len(refs.qualified)} qualified object(s) exist; "
                f"{len(refs.unqualified)} unqualified reference(s) unverified"
            ),
            expected="fully qualify objects so existence can be verified",
            remediation="Qualify with database.schema so Plumb can confirm existence.",
        )
    return build_result(
        ctx, "S-META-001", Status.PASS, observed="all referenced objects exist"
    )


@register_check(
    check_id="S-META-002",
    name="Join key data types are compatible",
    family=CheckFamily.METADATA,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.METADATA,
)
def s_meta_002(ctx: CheckContext, params: dict):
    skip = _needs_session(ctx, "S-META-002")
    if skip is not None:
        return skip
    try:
        tree = parse_one(ctx.sql_text or "")
    except SqlParseError as exc:
        return error(ctx, "S-META-002", f"could not parse SQL: {exc}")

    alias_map = _alias_to_ref(tree)
    join_pairs = _equi_join_columns(tree)
    if not join_pairs:
        return build_result(
            ctx, "S-META-002", Status.SKIP, observed="no equi-join key pairs found"
        )

    try:
        col_types = _column_types(ctx, alias_map)
    except Exception as exc:  # noqa: BLE001 - surface as ERROR
        return error(ctx, "S-META-002", f"column metadata lookup failed: {exc}")

    mismatches: list[str] = []
    unresolved = 0
    for (la, lc), (ra, rc) in join_pairs:
        lt = col_types.get((la, lc.upper()))
        rt = col_types.get((ra, rc.upper()))
        if lt is None or rt is None:
            unresolved += 1
            continue
        if type_family(lt) != type_family(rt):
            mismatches.append(f"{la}.{lc} ({lt}) vs {ra}.{rc} ({rt})")

    if mismatches:
        return build_result(
            ctx,
            "S-META-002",
            Status.FAIL,
            observed=f"incompatible join key types: {'; '.join(mismatches)}",
            expected="join keys share a type family",
            remediation="Align the column types or cast explicitly and intentionally.",
        )
    if unresolved:
        return build_result(
            ctx,
            "S-META-002",
            Status.WARN,
            observed=f"{unresolved} join key pair(s) could not be type-resolved",
            expected="qualify join columns so their types can be checked",
        )
    return build_result(
        ctx, "S-META-002", Status.PASS, observed="all join key types compatible"
    )


@register_check(
    check_id="S-META-003",
    name="Referenced objects are not flagged deprecated",
    family=CheckFamily.METADATA,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.METADATA,
)
def s_meta_003(ctx: CheckContext, params: dict):
    skip = _needs_session(ctx, "S-META-003")
    if skip is not None:
        return skip
    try:
        refs = resolve_refs(ctx.sql_text or "")
    except SqlParseError as exc:
        return error(ctx, "S-META-003", f"could not parse SQL: {exc}")
    declared = {d.upper() for d in (getattr(ctx.ruleset, "deprecated_objects", []) or [])}
    flagged: list[str] = []
    try:
        for database, db_refs in refs.by_database().items():
            result = ctx.session.execute(tables_query(database, db_refs))
            for row in result.rows:
                fqn = f"{database}.{row['TABLE_SCHEMA']}.{row['TABLE_NAME']}".upper()
                comment = str(row.get("COMMENT", "")).upper()
                if fqn in declared or "DEPRECATED" in comment:
                    flagged.append(fqn)
    except Exception as exc:  # noqa: BLE001 - surface as ERROR
        return error(ctx, "S-META-003", f"metadata lookup failed: {exc}")

    # also catch declared-deprecated refs even if metadata lookup did not return them
    for ref in refs.qualified:
        if ref.fqn().upper() in declared and ref.fqn().upper() not in flagged:
            flagged.append(ref.fqn().upper())

    if flagged:
        return build_result(
            ctx,
            "S-META-003",
            Status.FAIL,
            observed=f"deprecated object(s) referenced: {', '.join(sorted(set(flagged)))}",
            expected="no deprecated objects referenced",
            remediation="Repoint to the certified replacement.",
        )
    return build_result(
        ctx, "S-META-003", Status.PASS, observed="no deprecated objects referenced"
    )


@register_check(
    check_id="S-META-004",
    name="Source is a certified or approved object",
    family=CheckFamily.METADATA,
    default_severity=Severity.MEDIUM,
    execution_type=ExecutionType.METADATA,
)
def s_meta_004(ctx: CheckContext, params: dict):
    if not ctx.sql_text:
        return build_result(ctx, "S-META-004", Status.SKIP, observed="no SQL provided")
    certified = {c.upper() for c in (getattr(ctx.ruleset, "certified_sources", []) or [])}
    if not certified:
        return build_result(
            ctx, "S-META-004", Status.SKIP, observed="no certified sources configured"
        )
    try:
        refs = resolve_refs(ctx.sql_text)
    except SqlParseError as exc:
        return error(ctx, "S-META-004", f"could not parse SQL: {exc}")
    non_certified = [
        ref.fqn() for ref in refs.qualified if ref.fqn().upper() not in certified
    ]
    if non_certified:
        return build_result(
            ctx,
            "S-META-004",
            Status.WARN,
            observed=f"non-certified source(s): {', '.join(sorted(non_certified))}",
            expected="build from certified or approved sources",
            remediation="Prefer a certified view or published source where one exists.",
        )
    return build_result(
        ctx, "S-META-004", Status.PASS, observed="all sources certified"
    )


def _alias_to_ref(tree: exp.Expression) -> dict[str, "object"]:
    """Map each table alias (or bare name) to its TableRef."""
    from plumb.checks._sql import TableRef

    mapping: dict[str, object] = {}
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.name.lower() in cte_names and not table.db:
            continue
        ref = TableRef(catalog=table.catalog or None, db=table.db or None, name=table.name)
        alias = (table.alias or table.name)
        mapping[alias.upper()] = ref
    return mapping


def _equi_join_columns(tree: exp.Expression):
    """List of ((alias, col), (alias, col)) from equi-join ON conditions."""
    pairs = []
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        for eq in on.find_all(exp.EQ):
            left, right = eq.left, eq.right
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                la = (left.table or "").upper()
                ra = (right.table or "").upper()
                if la and ra:
                    pairs.append(((la, left.name), (ra, right.name)))
    return pairs


def _column_types(ctx: CheckContext, alias_map: dict[str, object]) -> dict[tuple[str, str], str]:
    from plumb.checks._metadata import ResolvedRefs
    from plumb.checks._sql import TableRef

    by_db: dict[str, list[TableRef]] = {}
    alias_by_ref: dict[str, str] = {}
    for alias, ref in alias_map.items():
        assert isinstance(ref, TableRef)
        if ref.is_qualified():
            by_db.setdefault(ref.db_or_catalog(), []).append(ref)
            alias_by_ref[ref.fqn().upper()] = alias

    types: dict[tuple[str, str], str] = {}
    resolved = ResolvedRefs(qualified=[r for refs in by_db.values() for r in refs])
    for database, refs in resolved.by_database().items():
        result = ctx.session.execute(columns_query(database, refs))
        for row in result.rows:
            fqn = f"{database}.{row['TABLE_SCHEMA']}.{row['TABLE_NAME']}".upper()
            matched_alias = alias_by_ref.get(fqn)
            if matched_alias:
                types[(matched_alias, str(row["COLUMN_NAME"]).upper())] = str(row["DATA_TYPE"])
    return types

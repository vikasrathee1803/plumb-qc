"""Shared SQL helpers for parsing the target and building check queries.

Execution checks wrap the analyst's query as a CTE named __plumb_target
and select from it, so a check never has to re-derive the query body and
the read-only guard sees one SELECT. All rendering goes through sqlglot in
the snowflake dialect, so a query that cannot be parsed is caught early.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

TARGET_CTE = "__plumb_target"


class SqlParseError(Exception):
    """The target SQL could not be parsed as a single statement."""


def parse_one(sql: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, read="snowflake") if s is not None]
    except ParseError as exc:
        raise SqlParseError(str(exc)) from exc
    if len(statements) != 1:
        raise SqlParseError(
            f"expected exactly one statement, found {len(statements)}"
        )
    return statements[0]


def render(expression: exp.Expression) -> str:
    return expression.sql(dialect="snowflake")


def _quote(identifier: str) -> str:
    return exp.column(identifier).sql(dialect="snowflake")


def wrap_target(sql: str, body: str) -> str:
    """Build 'WITH __plumb_target AS (<sql>) <body>'. The target is parsed
    first so an unparseable query fails before we ever touch a session."""
    inner = parse_one(sql)
    if not isinstance(inner, (exp.Select, exp.SetOperation, exp.Subquery)):
        raise SqlParseError(
            f"target must be a SELECT read, got {type(inner).__name__}"
        )
    return f"WITH {TARGET_CTE} AS (\n{render(inner)}\n)\n{body}"


def grain_count_query(sql: str, keys: list[str]) -> str:
    key_list = ", ".join(_quote(k) for k in keys)
    body = (
        f"SELECT {key_list}, COUNT(*) AS __PLUMB_DUP_COUNT\n"
        f"FROM {TARGET_CTE}\n"
        f"GROUP BY {key_list}\n"
        f"HAVING COUNT(*) > 1\n"
        f"ORDER BY __PLUMB_DUP_COUNT DESC"
    )
    return wrap_target(sql, body)


def null_count_query(sql: str, columns: list[str]) -> str:
    total = "COUNT(*) AS __PLUMB_TOTAL"
    null_terms = ", ".join(
        f"SUM(CASE WHEN {_quote(c)} IS NULL THEN 1 ELSE 0 END) AS {_null_alias(c)}"
        for c in columns
    )
    body = f"SELECT {total}, {null_terms}\nFROM {TARGET_CTE}"
    return wrap_target(sql, body)


def _null_alias(column: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in column).upper()
    return f"__PLUMB_NULLS_{safe}"


def row_count_query(sql: str) -> str:
    return wrap_target(sql, f"SELECT COUNT(*) AS __PLUMB_ROWS\nFROM {TARGET_CTE}")


def full_dup_query(sql: str) -> str:
    # SELECT * so GROUP BY ALL groups by every column (the whole row). With
    # SELECT COUNT(*) there are no non-aggregate columns, so GROUP BY ALL
    # would group by nothing and always report one group for a non-empty
    # table. Verified against live Snowflake.
    body = (
        f"SELECT COUNT(*) AS __PLUMB_DUP_ROWS FROM (\n"
        f"  SELECT * FROM {TARGET_CTE} GROUP BY ALL HAVING COUNT(*) > 1\n"
        f")"
    )
    return wrap_target(sql, body)


def freshness_query(sql: str, event_ts_col: str) -> str:
    body = (
        f"SELECT MAX({_quote(event_ts_col)}) AS __PLUMB_MAX_TS, "
        f"CURRENT_TIMESTAMP() AS __PLUMB_NOW\nFROM {TARGET_CTE}"
    )
    return wrap_target(sql, body)


def select_all_query(sql: str, limit: int) -> str:
    return wrap_target(sql, f"SELECT * FROM {TARGET_CTE} LIMIT {int(limit)}")


def render_literal(value: object) -> str:
    """Render a Python value as a SQL literal via sqlglot, so quoting and
    escaping are correct and injection-safe for ruleset-supplied values."""
    return exp.convert(value).sql(dialect="snowflake")


def domain_violation_query(sql: str, column: str, allowed: list[object]) -> str:
    values = ", ".join(render_literal(v) for v in allowed)
    body = (
        f"SELECT COUNT(*) AS __PLUMB_VIOLATIONS\n"
        f"FROM {TARGET_CTE}\n"
        f"WHERE {_quote(column)} IS NOT NULL AND {_quote(column)} NOT IN ({values})"
    )
    return wrap_target(sql, body)


def range_violation_query(
    sql: str, column: str, low: object | None, high: object | None
) -> str:
    clauses = []
    if low is not None:
        clauses.append(f"{_quote(column)} < {render_literal(low)}")
    if high is not None:
        clauses.append(f"{_quote(column)} > {render_literal(high)}")
    predicate = " OR ".join(clauses) if clauses else "FALSE"
    body = (
        f"SELECT COUNT(*) AS __PLUMB_VIOLATIONS\n"
        f"FROM {TARGET_CTE}\n"
        f"WHERE {predicate}"
    )
    return wrap_target(sql, body)


def orphan_query(sql: str, fk_column: str, ref_table: str, ref_column: str) -> str:
    body = (
        f"SELECT COUNT(*) AS __PLUMB_ORPHANS\n"
        f"FROM {TARGET_CTE} t\n"
        f"WHERE t.{_quote(fk_column)} IS NOT NULL\n"
        f"  AND NOT EXISTS (\n"
        f"    SELECT 1 FROM {ref_table} r WHERE r.{_quote(ref_column)} = t.{_quote(fk_column)}\n"
        f"  )"
    )
    return wrap_target(sql, body)


def render_target_template(template_sql: str, target_sql: str) -> str:
    """Replace {{ target }} in a ruleset metric query with the target as a
    parenthesized subquery, so a recon metric can run against the build."""
    inner = render(parse_one(target_sql))
    subquery = f"({inner})"
    out = template_sql
    for token in ("{{ target }}", "{{target}}", "{{  target  }}"):
        out = out.replace(token, subquery)
    return out


@dataclass(frozen=True)
class TableRef:
    catalog: str | None
    db: str | None
    name: str

    def fqn(self) -> str:
        parts = [p for p in (self.catalog, self.db, self.name) if p]
        return ".".join(parts)

    def db_or_catalog(self) -> str:
        """The database to resolve this ref against in INFORMATION_SCHEMA.
        For db.schema.table the catalog is the database; for schema.table
        the db part is the database."""
        return (self.catalog or self.db or "").upper()

    def is_qualified(self) -> bool:
        """True when both a database and schema are present, so the ref can
        be resolved against a specific INFORMATION_SCHEMA."""
        return bool(self.catalog and self.db)


def extract_table_refs(sql: str) -> list[TableRef]:
    """Distinct physical table and view references, excluding CTE names
    defined in the query itself."""
    tree = parse_one(sql)
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    refs: dict[str, TableRef] = {}
    for table in tree.find_all(exp.Table):
        if table.name.lower() in cte_names and not table.db and not table.catalog:
            continue
        ref = TableRef(
            catalog=table.catalog or None,
            db=table.db or None,
            name=table.name,
        )
        refs[ref.fqn().lower()] = ref
    return list(refs.values())

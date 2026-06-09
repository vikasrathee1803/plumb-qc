"""Reduce a complex build to the single read query Plumb checks.

A build is rarely a bare SELECT. It is often a view definition
(CREATE VIEW ... AS), a CREATE TABLE AS SELECT, or a multi-step script that
stages intermediate tables before the final one. Plumb's checks, lineage, and
read-only guarantees all operate on one SELECT, so this module folds such a
build into one equivalent query: each staged step becomes a CTE and the final
step (or trailing SELECT) becomes the body.

Nothing here executes. It only rewrites the analyst's SQL into the query whose
output is the build result, so the DDL itself never runs - the read-only guard
still sees a single SELECT downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

_READ_ROOTS: tuple[type[exp.Expression], ...] = (exp.Select, exp.SetOperation, exp.Subquery)


class BuildExtractError(Exception):
    """The build could not be reduced to a single analyzable read."""


@dataclass
class BuildQuery:
    sql: str
    notes: list[str] = field(default_factory=list)
    target_name: str | None = None  # the build's output object, when named


def _create_select(node: exp.Expression) -> exp.Expression | None:
    """The SELECT body of a CREATE ... AS (view or CTAS), else None."""
    if isinstance(node, exp.Create) and isinstance(node.expression, _READ_ROOTS):
        return node.expression
    return None


def _created_name(node: exp.Expression) -> str | None:
    """The (unqualified) name of the table or view a CREATE defines."""
    this = node.this
    table = this.this if isinstance(this, exp.Schema) else this
    return table.name if isinstance(table, exp.Table) else None


def _render(node: exp.Expression) -> str:
    return node.sql(dialect="snowflake")


def _kind(node: exp.Expression) -> str:
    if isinstance(node, exp.Create):
        return f"CREATE {node.args.get('kind') or 'TABLE'}".strip()
    return type(node).__name__


def _with_ctes(target: exp.Expression, steps: list[tuple[str, exp.Expression]]) -> str:
    """Render target with steps prepended as CTEs, merging into an existing
    WITH if the target already has one (avoids an illegal double WITH)."""
    target = target.copy()
    new_ctes = [
        exp.CTE(this=sel.copy(), alias=exp.TableAlias(this=exp.to_identifier(name)))
        for name, sel in steps
    ]
    existing = target.args.get("with")
    if isinstance(existing, exp.With):
        existing.set("expressions", new_ctes + list(existing.expressions))
    else:
        target.set("with", exp.With(expressions=new_ctes))
    return _render(target)


def extract_build_query(sql: str) -> BuildQuery:
    """Return the single read query for a build, with notes on what was folded.

    A bare SELECT passes through unchanged. A view/CTAS yields its SELECT body.
    A multi-statement script folds its staged CREATE ... AS steps into CTEs and
    analyzes the final step (or a trailing SELECT). Raises BuildExtractError if
    there is no read to analyze or the result is not a single SELECT.
    """
    if not sql or not sql.strip():
        raise BuildExtractError("no SQL provided")
    try:
        statements = [s for s in sqlglot.parse(sql, read="snowflake") if s is not None]
    except ParseError as exc:
        raise BuildExtractError(str(exc)) from exc
    if not statements:
        raise BuildExtractError("no SQL statements found")

    if len(statements) == 1 and isinstance(statements[0], _READ_ROOTS):
        return BuildQuery(sql=sql)

    single_target = _created_name(statements[0]) if len(statements) == 1 else None

    steps: list[tuple[str, exp.Expression]] = []
    trailing: exp.Expression | None = None
    skipped = 0
    for i, stmt in enumerate(statements):
        sel = _create_select(stmt)
        if sel is not None:
            steps.append((_created_name(stmt) or f"__plumb_step_{i + 1}", sel))
            trailing = None  # a later staged step supersedes a prior bare SELECT
        elif isinstance(stmt, _READ_ROOTS):
            trailing = stmt
        else:
            skipped += 1  # USE, SET, comments, plain CREATE TABLE, INSERT, GRANT...

    if trailing is None and not steps:
        raise BuildExtractError(
            "found no SELECT to analyze. Plumb checks a read: give it the view "
            "body, the CREATE TABLE AS SELECT, or the final SELECT of the build."
        )

    if trailing is not None:
        target, cte_steps, what = trailing, steps, "the final SELECT"
        target_name = single_target
    else:
        target, cte_steps, what = steps[-1][1], steps[:-1], f"the build of {steps[-1][0]!r}"
        target_name = steps[-1][0]

    notes: list[str] = []
    if len(statements) == 1:
        notes.append(f"Analyzed the SELECT inside your {_kind(statements[0])}.")
    else:
        note = f"Analyzed {what} from your {len(statements)}-statement build"
        if cte_steps:
            note += f"; folded {len(cte_steps)} staged step(s) into CTEs"
        if skipped:
            note += f"; skipped {skipped} non-read statement(s)"
        notes.append(note + ".")

    build_sql = _render(target) if not cte_steps else _with_ctes(target, cte_steps)

    try:
        rebuilt = [s for s in sqlglot.parse(build_sql, read="snowflake") if s is not None]
    except ParseError as exc:
        raise BuildExtractError(f"could not assemble one query from the build: {exc}") from exc
    if len(rebuilt) != 1 or not isinstance(rebuilt[0], _READ_ROOTS):
        raise BuildExtractError("could not reduce the build to a single SELECT")
    return BuildQuery(sql=build_sql, notes=notes, target_name=target_name)

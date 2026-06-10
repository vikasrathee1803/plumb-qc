"""Projection extraction from custom SQL (PARITY-PLAN-V2 S9.1, D15).

v1 row-counts custom SQL relations only — an honest coverage gap. Many
real custom SQL relations are plain SELECTs whose projected column names
can be read statically, which is enough to extend null/aggregate parity
to them: metrics.py wraps the SQL verbatim and references the output
columns by name, so all this module must produce is the list of output
column names it can vouch for.

The contract is refuse-and-degrade (D15): `extract_projected_columns`
NEVER raises. Anything it cannot prove — unparseable SQL, multiple
statements, a non-SELECT, a star in the final projection — returns None
and the caller keeps the v1 row-count-only behavior. That keeps the v1
coverage gap acceptable on complex SQL instead of turning it into a run
failure.

What is deliberately skipped rather than refused: aggregate and window
expressions. Their output columns exist, but re-aggregating them in the
metrics wrapper would compute aggregates of aggregates (plan section 2
item 4), so they are dropped from the returned list. A projection that
is ALL aggregates returns [] (not None): the relation is parseable but
contributes no column metrics — row-count-only, same as v1.

Names are returned upper-cased (Snowflake canonical, consistent with
mapping.py). The caller must still treat them as untrusted text and
quote them with metrics._quote_ident: an alias may legally contain any
character, including double quotes.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def extract_projected_columns(sql: str) -> list[str] | None:
    """Output column names of a custom SQL SELECT, or None to refuse.

    Returns None when the SQL cannot be proven to be a single SELECT
    (or CTE-wrapped SELECT / set operation) with a fully-named final
    projection: parse failure, multiple statements, a non-SELECT
    statement, or any star (bare `*` or qualified `t.*`) in the final
    projection. Set operations take the LEFTMOST branch — its names
    define the result columns.

    Aggregate and window expressions are skipped (see module docstring);
    an aggregate-only projection returns []. Output names that collide —
    whether between kept expressions or with a skipped aggregate's name —
    are dropped entirely: referencing an ambiguous name in the metrics
    wrapper would error in the warehouse, so be conservative. Never
    raises: any sqlglot exception means None.
    """
    try:
        statements = [
            statement
            for statement in sqlglot.parse(sql, dialect="snowflake")
            if statement is not None
        ]
    except Exception:
        # D15: any sqlglot failure (ParseError, tokenizer errors, anything
        # else) means "cannot prove the projection" — degrade silently.
        return None
    if len(statements) != 1:
        return None
    select = _final_select(statements[0])
    if select is None:
        return None

    kept: list[str] = []
    name_counts: dict[str, int] = {}
    for expression in select.expressions:
        if isinstance(expression, exp.Star):
            return None
        if isinstance(expression, exp.Column) and isinstance(expression.this, exp.Star):
            return None
        name = _output_name(expression)
        if not name:
            # An output column with no derivable name cannot be referenced
            # in the metrics wrapper; refuse the whole projection.
            return None
        name_counts[name] = name_counts.get(name, 0) + 1
        # Containment (not just isinstance) so SUM(a) + 1 is also skipped:
        # re-aggregating any aggregate-bearing expression is wrong.
        if expression.find(exp.AggFunc, exp.Window) is not None:
            continue
        kept.append(name)
    # A name that occurs more than once across the WHOLE projection
    # (kept or skipped) is dropped: the wrapper reference would be
    # ambiguous in the warehouse. Order of the survivors is preserved.
    return [name for name in kept if name_counts[name] == 1]


def _final_select(statement: exp.Expression) -> exp.Select | None:
    """The SELECT whose projection names the statement's output columns.

    Unwraps parenthesized selects and walks set operations down their
    leftmost branch; a CTE-wrapped SELECT is already an exp.Select in
    sqlglot (the WITH clause hangs off the select itself). Anything else
    (UPDATE, INSERT, DDL, sqlglot Command fallback) returns None.
    """
    node: exp.Expression | None = statement
    while node is not None:
        if isinstance(node, exp.Select):
            return node
        if isinstance(node, (exp.Subquery, exp.SetOperation)):
            node = node.this
            continue
        return None
    return None


def _output_name(expression: exp.Expression) -> str:
    """The warehouse-visible output column name, upper-cased.

    Alias wins; a plain column reference contributes its column part
    (the table qualifier is not part of the output name); any other
    expression is named by its SQL text, which is how Snowflake labels
    unaliased expression columns. Upper-casing matches how unquoted
    identifiers fold in Snowflake — a quoted lower-case alias will
    therefore not match at query time, and the metrics wrapper degrades
    to row-count-only when the warehouse rejects the reference.
    """
    if isinstance(expression, exp.Alias):
        return expression.alias.upper()
    if isinstance(expression, exp.Column):
        return expression.name.upper()
    return expression.sql(dialect="snowflake").upper()

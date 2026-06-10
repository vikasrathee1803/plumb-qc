"""Parity metric measurement for one object on one side (PARITY-PLAN S3.1).

Given a ResolvedObject and a live session, `measure` discovers the object's
columns through INFORMATION_SCHEMA and runs one aggregate query (row count,
per-column null counts, numeric SUM/MIN/MAX, COUNT DISTINCT on declared
keys) plus an optional grain-grouped top-N query, returning a ParityMetrics.

Normalization rule: the returned metrics ALWAYS report columns/keys/grain
under the LEGACY (old) names regardless of side — on the target side the
declared column_map is applied when building SQL (old -> new) and
reverse-applied on results (new -> old), so the two sides are directly
comparable by the M-* checks. `object_fqn` is the canonical upper-cased FQN
actually queried, or "custom-sql" for custom SQL relations.

Custom SQL relations run the workbook's SELECT verbatim on both sides,
wrapped only for a row count (PARITY-PLAN D6/D7 — an honest v1 gap: no
column metrics, no distinct, no grain). Custom SQL containing a semicolon
outside string literals or quoted identifiers is refused before wrapping
as a cheap multi-statement guard; connect.snowflake.assert_read_only on
the session remains the real gate.

Identifier safety: table and column names arrive from workbook XML and
map.yml, so every identifier is emitted upper-cased and double-quoted with
internal double quotes doubled, and FQN parts are quoted individually.
Schema/table literals in the discovery query are single-quoted with
internal quotes doubled. SQL generation is deterministic: columns iterate
in sorted reported-name order with positional aliases, so identical inputs
produce byte-identical statements.

A declared key or grain column missing from the discovered columns raises
ParityMetricsError naming the column(s) and the object: silently omitting
a declared key (or measuring a partial grain) would let a check pass while
proving less than the map promised.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from plumb.parity.contracts import (
    ColumnMetrics,
    GrainGroup,
    ParityMetrics,
    ResolvedObject,
)
from plumb.parity.mapping import parse_fqn

NULL_GROUP_VALUE = "∅"

# DATA_TYPE values from INFORMATION_SCHEMA.COLUMNS that get SUM/MIN/MAX.
_NUMERIC_DATA_TYPES = frozenset(
    {
        "NUMBER",
        "DECIMAL",
        "NUMERIC",
        "INT",
        "INTEGER",
        "BIGINT",
        "SMALLINT",
        "TINYINT",
        "BYTEINT",
        "FLOAT",
        "FLOAT4",
        "FLOAT8",
        "DOUBLE",
        "DOUBLE PRECISION",
        "REAL",
    }
)


class ParityMetricsError(Exception):
    """A parity metric could not be measured for one relation.

    The runner records the message per relation; the M-* checks turn it
    into FAIL/ERROR evidence (e.g. M-SCHEMA-001 for a missing object)."""


def _quote_ident(name: str) -> str:
    """Upper-cased, double-quoted identifier with internal quotes doubled.

    Names come from workbook XML and map.yml — always untrusted."""
    return '"' + name.upper().replace('"', '""') + '"'


def _quote_fqn(parts: tuple[str, str, str]) -> str:
    return ".".join(_quote_ident(part) for part in parts)


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_numeric(data_type: str) -> bool:
    return data_type.upper().split("(")[0].strip() in _NUMERIC_DATA_TYPES


def _has_bare_semicolon(sql: str) -> bool:
    """True when sql contains a semicolon outside single-quoted string
    literals and double-quoted identifiers. Doubled quotes ('' / "") fall
    out of the simple toggle naturally because no character sits between
    the pair. Backslash escapes and comments are not modelled — a semicolon
    inside either is refused, which errs toward rejection, never injection."""
    in_single = False
    in_double = False
    for char in sql:
        if in_single:
            in_single = char != "'"
        elif in_double:
            in_double = char != '"'
        elif char == "'":
            in_single = True
        elif char == '"':
            in_double = True
        elif char == ";":
            return True
    return False


def _execute(session: Any, sql: str, fqn: str) -> list[dict[str, Any]]:
    try:
        result = session.execute(sql)
    except Exception as exc:
        raise ParityMetricsError(f"query failed for {fqn}: {exc}") from exc
    return list(result.rows)


def _as_int(value: Any) -> int:
    return int(value) if value is not None else 0


def _as_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def discovery_sql(parts: tuple[str, str, str]) -> str:
    """Column discovery via the object's database INFORMATION_SCHEMA."""
    database, schema, table = parts
    return (
        "SELECT COLUMN_NAME, DATA_TYPE\n"
        f"FROM {_quote_ident(database)}.INFORMATION_SCHEMA.COLUMNS\n"
        f"WHERE TABLE_SCHEMA = {_quote_literal(schema.upper())} "
        f"AND TABLE_NAME = {_quote_literal(table.upper())}\n"
        "ORDER BY COLUMN_NAME"
    )


def aggregate_sql(
    quoted_fqn: str,
    columns: list[tuple[str, str, str]],
    keys: list[tuple[str, str]],
) -> str:
    """One whole-table aggregate. `columns` is (reported, physical,
    data_type) sorted by reported name; `keys` is (reported, physical)
    sorted by reported name. Aliases are positional (NULL_i, SUM_i, MIN_i,
    MAX_i per column index; DIST_j per key index) so results map back by
    position with no collision risk from untrusted names."""
    items = ["COUNT(*) AS ROW_COUNT"]
    for index, (_, physical, data_type) in enumerate(columns):
        quoted = _quote_ident(physical)
        items.append(f"COUNT_IF({quoted} IS NULL) AS NULL_{index}")
        if _is_numeric(data_type):
            items.append(f"SUM({quoted}) AS SUM_{index}")
            items.append(f"MIN({quoted}) AS MIN_{index}")
            items.append(f"MAX({quoted}) AS MAX_{index}")
    for index, (_, physical) in enumerate(keys):
        items.append(f"COUNT(DISTINCT {_quote_ident(physical)}) AS DIST_{index}")
    return "SELECT\n  " + ",\n  ".join(items) + f"\nFROM {quoted_fqn}"


def grain_sql(quoted_fqn: str, grain_physical: tuple[str, ...], top_n: int) -> str:
    """Top-N grouped counts on the declared grain, deterministically
    ordered (count desc, then group values via positional aliases)."""
    select_items = [
        f"{_quote_ident(physical)} AS G_{index}"
        for index, physical in enumerate(grain_physical)
    ]
    group_by = ", ".join(_quote_ident(physical) for physical in grain_physical)
    order_tail = ", ".join(f"G_{index}" for index in range(len(grain_physical)))
    return (
        "SELECT " + ", ".join(select_items) + ", COUNT(*) AS GROUP_COUNT\n"
        f"FROM {quoted_fqn}\n"
        f"GROUP BY {group_by}\n"
        f"ORDER BY COUNT(*) DESC, {order_tail}\n"
        f"LIMIT {int(top_n)}"
    )


def custom_sql_count_sql(custom_sql: str) -> str:
    """Wrap the workbook's custom SQL verbatim for a row count, refusing
    multi-statement text up front (assert_read_only is the real gate)."""
    text = custom_sql.strip()
    if not text:
        raise ParityMetricsError("custom SQL relation has no SQL text")
    if _has_bare_semicolon(text):
        raise ParityMetricsError(
            "custom SQL contains a semicolon outside string literals; "
            "refusing to wrap potentially multi-statement SQL"
        )
    return f"SELECT COUNT(*) AS ROW_COUNT\nFROM (\n{text}\n) AS PLUMB_PARITY_SRC"


def measure(
    session: Any,
    resolved: ResolvedObject,
    side: Literal["legacy", "target"],
    *,
    grain_top_n: int = 20,
) -> ParityMetrics:
    """Measure one resolved relation on one side, normalized to legacy names.

    Raises ParityMetricsError for: an unmeasurable relation, an object
    missing from INFORMATION_SCHEMA, refused custom SQL, or any query
    failure (wrapped with the FQN and the underlying message)."""
    relation = resolved.relation
    if relation.kind == "custom_sql":
        return _measure_custom_sql(session, relation.custom_sql or "")
    if relation.kind != "table":
        raise ParityMetricsError(
            f"relation {relation.label} is not measurable (kind={relation.kind!r})"
        )

    fqn = relation.fqn if side == "legacy" else resolved.target_fqn
    if not fqn:
        raise ParityMetricsError(
            f"relation {relation.label} has no {side}-side object name"
        )
    try:
        parts = parse_fqn(fqn)
    except ValueError as exc:
        raise ParityMetricsError(f"cannot measure {fqn!r}: {exc}") from exc
    canonical_fqn = ".".join(part.upper() for part in parts)
    quoted_fqn = _quote_fqn(parts)

    # old (legacy, reported) -> new (physical on the target side); identity
    # on the legacy side so one code path serves both.
    old_to_physical: dict[str, str] = (
        {old.upper(): new.upper() for old, new in resolved.column_map.items()}
        if side == "target"
        else {}
    )
    physical_to_old = {new: old for old, new in old_to_physical.items()}

    discovered = _discover_columns(session, parts, canonical_fqn)
    # (reported legacy name, physical name, data type), sorted by reported.
    columns = sorted(
        (physical_to_old.get(physical, physical), physical, data_type)
        for physical, data_type in discovered.items()
    )
    available = set(discovered)
    declared_keys = sorted({key.upper() for key in resolved.keys})
    missing_keys = [
        key for key in declared_keys if old_to_physical.get(key, key) not in available
    ]
    if missing_keys:
        raise ParityMetricsError(
            f"declared key column(s) not found on {canonical_fqn}: "
            f"{', '.join(missing_keys)}"
        )
    keys = [(key, old_to_physical.get(key, key)) for key in declared_keys]

    agg = aggregate_sql(quoted_fqn, columns, keys)
    rows = _execute(session, agg, canonical_fqn)
    if not rows:
        raise ParityMetricsError(f"aggregate query returned no rows for {canonical_fqn}")
    row = rows[0]

    column_metrics: dict[str, ColumnMetrics] = {}
    for index, (reported, _, data_type) in enumerate(columns):
        metrics = ColumnMetrics(
            data_type=data_type, null_count=_as_int(row.get(f"NULL_{index}"))
        )
        if _is_numeric(data_type):
            metrics.sum_value = _as_float(row.get(f"SUM_{index}"))
            metrics.min_value = _as_float(row.get(f"MIN_{index}"))
            metrics.max_value = _as_float(row.get(f"MAX_{index}"))
        column_metrics[reported] = metrics
    distinct_counts = {
        reported: _as_int(row.get(f"DIST_{index}"))
        for index, (reported, _) in enumerate(keys)
    }

    grain = tuple(name.upper() for name in resolved.grain)
    grain_physical = tuple(old_to_physical.get(name, name) for name in grain)
    missing_grain = [
        name
        for name, physical in zip(grain, grain_physical, strict=True)
        if physical not in available
    ]
    if missing_grain:
        raise ParityMetricsError(
            f"declared grain column(s) not found on {canonical_fqn}: "
            f"{', '.join(missing_grain)}"
        )
    grain_groups: list[GrainGroup] = []
    if grain:
        grain_rows = _execute(
            session, grain_sql(quoted_fqn, grain_physical, grain_top_n), canonical_fqn
        )
        for grain_row in grain_rows:
            group = {
                reported: _stringify(grain_row.get(f"G_{index}"))
                for index, reported in enumerate(grain)
            }
            grain_groups.append(
                GrainGroup(group=group, count=_as_int(grain_row.get("GROUP_COUNT")))
            )

    return ParityMetrics(
        object_fqn=canonical_fqn,
        row_count=_as_int(row.get("ROW_COUNT")),
        columns=column_metrics,
        distinct_counts=distinct_counts,
        grain_groups=grain_groups,
    )


def _discover_columns(
    session: Any, parts: tuple[str, str, str], canonical_fqn: str
) -> dict[str, str]:
    """Physical column name (upper) -> DATA_TYPE via INFORMATION_SCHEMA.
    An empty result means the object does not exist on this side."""
    rows = _execute(session, discovery_sql(parts), canonical_fqn)
    if not rows:
        raise ParityMetricsError(f"object not found: {canonical_fqn}")
    return {
        str(row["COLUMN_NAME"]).upper(): str(row["DATA_TYPE"]).upper() for row in rows
    }


def _measure_custom_sql(session: Any, custom_sql: str) -> ParityMetrics:
    sql = custom_sql_count_sql(custom_sql)
    rows = _execute(session, sql, "custom-sql")
    if not rows:
        raise ParityMetricsError("row count query returned no rows for custom-sql")
    return ParityMetrics(
        object_fqn="custom-sql", row_count=_as_int(rows[0].get("ROW_COUNT"))
    )


def _stringify(value: Any) -> str:
    """Canonical grain-group value string, identical across drivers.

    Numeric values (int / float / Decimal) stringify canonically so the
    same warehouse value compares equal whatever Python type the driver
    returned: integral values as their integer string ("5" for Decimal('5'),
    5.0, and 5), non-integral values as repr(float(value)). NULL stays the
    NULL_GROUP_VALUE sentinel; booleans and everything else keep str()."""
    if value is None:
        return NULL_GROUP_VALUE
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, Decimal)):
        as_float = float(value)
        if as_float.is_integer():
            return str(int(as_float))
        return repr(as_float)
    return str(value)

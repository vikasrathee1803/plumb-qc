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
wrapped as a subquery. v1 measured a row count only (PARITY-PLAN D6/D7);
when sql_projection can statically name the projection's output columns
(PARITY-PLAN-V2 S9.2 / D15), per-column null counts and numeric
SUM/MIN/MAX are measured too. Column types cannot come from
INFORMATION_SCHEMA (it cannot describe a subquery) and the session's
read-only guard refuses DESCRIBE, so types are proven in-band with
MAX(SYSTEM$TYPEOF(column)) over the wrapped SQL — a plain SELECT the
guard accepts. Any failure to prove a type or to measure degrades
silently to the v1 row-count-only metrics: never guess a type, never
fail the run for optional coverage (the checks layer already reports
column-less custom SQL as row-count-only coverage). Distinct counts and
grain stay out of scope for custom SQL. Custom SQL containing a
semicolon outside string literals or quoted identifiers is refused
before wrapping as a cheap multi-statement guard;
connect.snowflake.assert_read_only on the session remains the real gate.

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

import json
from decimal import Decimal
from typing import Any, Literal

from plumb.parity.contracts import (
    ColumnMetrics,
    GrainGroup,
    ParityMetrics,
    ResolvedObject,
)
from plumb.parity.mapping import parse_fqn
from plumb.parity.sql_projection import extract_projected_columns

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


def row_hash_sql(
    quoted_fqn: str,
    keys: list[tuple[str, str]],
    columns: list[tuple[str, str, str]],
    cap: int,
) -> str:
    """Per-row fingerprints for the first `cap` rows by key order
    (M-HASH-001). `keys` is (reported, physical) and `columns` is
    (reported, physical, data_type), both sorted by reported name so the
    two sides hash the same logical columns in the same order whatever
    the physical renames. HASH(...) is Snowflake's deterministic 64-bit
    hash: it handles NULLs and types natively (no TO_VARCHAR formatting
    ambiguity, no separator/sentinel games) and only the hash ever leaves
    the warehouse. Key columns come back under positional K_i aliases;
    ORDER BY uses the aliases so the window is deterministic."""
    key_items = [
        f"{_quote_ident(physical)} AS K_{index}"
        for index, (_, physical) in enumerate(keys)
    ]
    hash_args = ", ".join(_quote_ident(physical) for _, physical, _ in columns)
    order_tail = ", ".join(f"K_{index}" for index in range(len(keys)))
    return (
        "SELECT " + ", ".join(key_items) + ",\n"
        f"  TO_VARCHAR(HASH({hash_args})) AS ROW_HASH\n"
        f"FROM {quoted_fqn}\n"
        f"ORDER BY {order_tail}\n"
        f"LIMIT {int(cap)}"
    )


def custom_sql_from_clause(text: str) -> str:
    """The wrapped FROM target every custom-SQL statement queries. The
    workbook's SQL runs verbatim inside it, byte-identical on both sides;
    only the wrapper around it differs by statement."""
    return f"(\n{text}\n) AS PLUMB_PARITY_SRC"


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
    return f"SELECT COUNT(*) AS ROW_COUNT\nFROM {custom_sql_from_clause(text)}"


def custom_sql_probe_sql(text: str, names: list[str]) -> str:
    """Row count plus per-column type proof for a custom SQL projection.

    MAX(SYSTEM$TYPEOF(col)) returns the column's logical type with a
    physical-representation suffix (e.g. 'NUMBER(38,0)[SB16]'); the
    logical prefix is the expression's static type, so it is constant
    across rows and MAX is a deterministic way to aggregate it into the
    single result row. Over an empty result MAX is NULL — exactly the
    "cannot prove a type" signal the caller degrades on. `names` come
    from sqlglot output and are untrusted text: every one goes through
    _quote_ident (TYPE_i aliases are positional, never derived from the
    name). The caller has already validated `text` via
    custom_sql_count_sql."""
    items = ["COUNT(*) AS ROW_COUNT"]
    for index, name in enumerate(names):
        items.append(f"MAX(SYSTEM$TYPEOF({_quote_ident(name)})) AS TYPE_{index}")
    return "SELECT\n  " + ",\n  ".join(items) + f"\nFROM {custom_sql_from_clause(text)}"


def _typeof_data_type(value: Any) -> str | None:
    """Logical DATA_TYPE from a SYSTEM$TYPEOF result ('NUMBER(38,0)[SB16]'
    -> 'NUMBER(38,0)'), or None when the type cannot be proven (NULL or
    empty — e.g. the custom SQL returned no rows). None means degrade:
    a guessed type could emit SUM over a non-numeric column."""
    if value is None:
        return None
    text = str(value).split("[", 1)[0].strip().upper()
    return text or None


def measure(
    session: Any,
    resolved: ResolvedObject,
    side: Literal["legacy", "target"],
    *,
    grain_top_n: int = 20,
    hash_cap: int = 1000,
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

    row_hashes: dict[str, str] = {}
    hashed_columns: list[str] = []
    hash_error: str | None = None
    if keys and hash_cap > 0:
        # Best-effort by design: a hash capture failure is M-HASH-001's
        # ERROR evidence, never a reason to lose the aggregate metrics
        # measured above.
        hashed_columns = [reported for reported, _, _ in columns]
        try:
            hash_rows = _execute(
                session, row_hash_sql(quoted_fqn, keys, columns, hash_cap), canonical_fqn
            )
            for hash_row in hash_rows:
                key_obj = {
                    reported: _stringify(hash_row.get(f"K_{index}"))
                    for index, (reported, _) in enumerate(keys)
                }
                key_json = json.dumps(key_obj, sort_keys=True)
                if key_json in row_hashes:
                    # A non-unique declared key makes the window and the
                    # per-key comparison meaningless — and is itself news.
                    raise ParityMetricsError(
                        f"declared key is not unique within the hash window "
                        f"on {canonical_fqn}: duplicate {key_json}"
                    )
                row_hashes[key_json] = str(hash_row.get("ROW_HASH"))
        except ParityMetricsError as exc:
            row_hashes = {}
            hash_error = str(exc)

    return ParityMetrics(
        object_fqn=canonical_fqn,
        row_count=_as_int(row.get("ROW_COUNT")),
        columns=column_metrics,
        distinct_counts=distinct_counts,
        grain_groups=grain_groups,
        row_hashes=row_hashes,
        hashed_columns=hashed_columns,
        hash_error=hash_error,
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
    """Measure a custom SQL relation: column metrics when the projection
    is statically parseable (S9.2), v1 row-count-only otherwise.

    custom_sql_count_sql runs first for its refusals (empty text, bare
    semicolon) so the v1 refusal behavior is unchanged whatever the
    projection parser thinks of the text."""
    count_sql = custom_sql_count_sql(custom_sql)
    text = custom_sql.strip()
    names = extract_projected_columns(text)
    if names:
        measured = _measure_custom_sql_columns(session, text, sorted(names))
        if measured is not None:
            return measured
    rows = _execute(session, count_sql, "custom-sql")
    if not rows:
        raise ParityMetricsError("row count query returned no rows for custom-sql")
    return ParityMetrics(
        object_fqn="custom-sql", row_count=_as_int(rows[0].get("ROW_COUNT"))
    )


def _measure_custom_sql_columns(
    session: Any, text: str, names: list[str]
) -> ParityMetrics | None:
    """Column metrics for a custom SQL whose projection parsed, or a
    degraded result; never raises (D15: optional coverage must not fail
    the run — the plain count path still raises for a relation that
    cannot be measured at all).

    Returns None only when the probe query itself failed or returned no
    row: the caller falls back to the v1 count query, whose failure is a
    real measurement error. After a successful probe, any type that
    cannot be proven and any aggregate failure degrade to row-count-only
    using the probe's COUNT(*) — same semantics as the count query, no
    extra statement, and nothing misleading recorded."""
    try:
        probe_rows = _execute(session, custom_sql_probe_sql(text, names), "custom-sql")
    except ParityMetricsError:
        return None
    if not probe_rows:
        return None
    probe = probe_rows[0]
    row_count_only = ParityMetrics(
        object_fqn="custom-sql", row_count=_as_int(probe.get("ROW_COUNT"))
    )

    # (reported, physical, data_type): reported == physical — custom SQL
    # has no column_map, the projection's output names are queried as-is.
    columns: list[tuple[str, str, str]] = []
    for index, name in enumerate(names):
        data_type = _typeof_data_type(probe.get(f"TYPE_{index}"))
        if data_type is None:
            return row_count_only
        columns.append((name, name, data_type))

    agg = aggregate_sql(custom_sql_from_clause(text), columns, [])
    try:
        rows = _execute(session, agg, "custom-sql")
    except ParityMetricsError:
        return row_count_only
    if not rows:
        return row_count_only
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
    return ParityMetrics(
        object_fqn="custom-sql",
        row_count=_as_int(row.get("ROW_COUNT")),
        columns=column_metrics,
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

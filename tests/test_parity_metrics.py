"""Tests for the parity metric measurement layer (plumb/parity/metrics.py).

Invariants under test: SQL generation is deterministic and identifier-safe;
every generated statement passes the read-only guard; results are always
normalized to the legacy (old) column names regardless of side; custom SQL
is row-count-only and refuses bare semicolons; a missing object raises
ParityMetricsError. RouteSession answers each query by SQL substring.
"""

from __future__ import annotations

from typing import Any

import pytest

from plumb.connect.snowflake import assert_read_only
from plumb.parity.contracts import ResolvedObject, SourceRelation
from plumb.parity.metrics import (
    NULL_GROUP_VALUE,
    ParityMetricsError,
    measure,
)
from tests._fakes import RouteSession

LEGACY_FQN = "LEGACY_DB.SALES.ORDERS"
TARGET_FQN = "GALAXY_DB.PRESENTATION.FCT_ORDERS"


def table_relation(
    database: str = "LEGACY_DB",
    schema: str = "SALES",
    table: str = "ORDERS",
) -> SourceRelation:
    return SourceRelation(
        datasource="ds1",
        kind="table",
        database=database,
        schema=schema,
        table=table,
        connection_class="snowflake",
    )


def resolved_table(**overrides: Any) -> ResolvedObject:
    fields: dict[str, Any] = {
        "relation": table_relation(),
        "target_fqn": TARGET_FQN,
        "column_map": {"REGION": "SALES_REGION"},
        "keys": ("ORDER_ID",),
        "grain": ("ORDER_DATE", "REGION"),
    }
    fields.update(overrides)
    return ResolvedObject(**fields)


def resolved_custom_sql(sql: str) -> ResolvedObject:
    relation = SourceRelation(
        datasource="ds1",
        kind="custom_sql",
        custom_sql=sql,
        connection_class="snowflake",
    )
    return ResolvedObject(relation=relation, target_fqn="", via_identity=True)


def discovery_rows(region_name: str = "REGION") -> list[dict[str, Any]]:
    return [
        {"COLUMN_NAME": "AMOUNT", "DATA_TYPE": "FLOAT"},
        {"COLUMN_NAME": "ORDER_DATE", "DATA_TYPE": "DATE"},
        {"COLUMN_NAME": "ORDER_ID", "DATA_TYPE": "NUMBER"},
        {"COLUMN_NAME": region_name, "DATA_TYPE": "TEXT"},
    ]


# Sorted reported columns: AMOUNT=0, ORDER_DATE=1, ORDER_ID=2, REGION=3.
AGGREGATE_ROW: dict[str, Any] = {
    "ROW_COUNT": 100,
    "NULL_0": 0,
    "SUM_0": 5000.5,
    "MIN_0": 1.5,
    "MAX_0": 250.0,
    "NULL_1": 2,
    "NULL_2": 0,
    "SUM_2": 5050,
    "MIN_2": 1,
    "MAX_2": 100,
    "NULL_3": 5,
    "DIST_0": 100,
}

GRAIN_ROWS: list[dict[str, Any]] = [
    {"G_0": "2026-01-01", "G_1": "EMEA", "GROUP_COUNT": 60},
    {"G_0": "2026-01-02", "G_1": None, "GROUP_COUNT": 40},
]


def table_session(region_name: str = "REGION") -> RouteSession:
    # Route order matters: most specific needles first.
    return (
        RouteSession()
        .add("INFORMATION_SCHEMA.COLUMNS", discovery_rows(region_name))
        .add("GROUP BY", GRAIN_ROWS)
        .add("COUNT(*) AS ROW_COUNT", [AGGREGATE_ROW])
    )


class TestTableMetricsLegacy:
    def test_full_metrics(self) -> None:
        session = table_session()
        metrics = measure(session, resolved_table(), "legacy")

        assert metrics.object_fqn == LEGACY_FQN
        assert metrics.row_count == 100
        assert sorted(metrics.columns) == ["AMOUNT", "ORDER_DATE", "ORDER_ID", "REGION"]

        amount = metrics.columns["AMOUNT"]
        assert amount.data_type == "FLOAT"
        assert amount.null_count == 0
        assert (amount.sum_value, amount.min_value, amount.max_value) == (5000.5, 1.5, 250.0)

        order_id = metrics.columns["ORDER_ID"]
        assert (order_id.sum_value, order_id.min_value, order_id.max_value) == (
            5050.0,
            1.0,
            100.0,
        )

        # Non-numeric columns carry null counts but no numeric aggregates.
        region = metrics.columns["REGION"]
        assert region.null_count == 5
        assert region.sum_value is None and region.min_value is None
        order_date = metrics.columns["ORDER_DATE"]
        assert order_date.null_count == 2
        assert order_date.sum_value is None

        assert metrics.distinct_counts == {"ORDER_ID": 100}
        assert [g.count for g in metrics.grain_groups] == [60, 40]
        assert metrics.grain_groups[0].group == {"ORDER_DATE": "2026-01-01", "REGION": "EMEA"}

    def test_exactly_three_queries(self) -> None:
        session = table_session()
        measure(session, resolved_table(), "legacy")
        assert len(session.executed) == 3
        assert "INFORMATION_SCHEMA.COLUMNS" in session.executed[0]
        assert "COUNT(*) AS ROW_COUNT" in session.executed[1]
        assert "GROUP BY" in session.executed[2]

    def test_legacy_sql_uses_legacy_fqn_and_quoted_identifiers(self) -> None:
        session = table_session()
        measure(session, resolved_table(), "legacy")
        aggregate = session.executed[1]
        assert '"LEGACY_DB"."SALES"."ORDERS"' in aggregate
        assert 'COUNT_IF("REGION" IS NULL)' in aggregate
        assert 'COUNT(DISTINCT "ORDER_ID") AS DIST_0' in aggregate

    def test_no_grain_query_when_grain_undeclared(self) -> None:
        session = table_session()
        metrics = measure(session, resolved_table(grain=()), "legacy")
        assert metrics.grain_groups == []
        assert len(session.executed) == 2

    def test_null_grain_value_reported_as_empty_set_symbol(self) -> None:
        session = table_session()
        metrics = measure(session, resolved_table(), "legacy")
        assert metrics.grain_groups[1].group == {
            "ORDER_DATE": "2026-01-02",
            "REGION": NULL_GROUP_VALUE,
        }

    def test_grain_top_n_in_limit(self) -> None:
        session = table_session()
        measure(session, resolved_table(), "legacy", grain_top_n=7)
        assert session.executed[2].endswith("LIMIT 7")


class TestTargetSideNormalization:
    def test_column_map_applied_in_sql(self) -> None:
        session = table_session(region_name="SALES_REGION")
        measure(session, resolved_table(), "target")
        aggregate = session.executed[1]
        grain = session.executed[2]
        assert '"GALAXY_DB"."PRESENTATION"."FCT_ORDERS"' in aggregate
        assert 'COUNT_IF("SALES_REGION" IS NULL)' in aggregate
        assert '"REGION"' not in aggregate
        assert 'GROUP BY "ORDER_DATE", "SALES_REGION"' in grain
        assert '"REGION"' not in grain

    def test_results_normalized_to_old_names(self) -> None:
        session = table_session(region_name="SALES_REGION")
        metrics = measure(session, resolved_table(), "target")
        assert metrics.object_fqn == TARGET_FQN
        assert sorted(metrics.columns) == ["AMOUNT", "ORDER_DATE", "ORDER_ID", "REGION"]
        assert "SALES_REGION" not in metrics.columns
        assert metrics.columns["REGION"].null_count == 5
        assert metrics.distinct_counts == {"ORDER_ID": 100}
        assert metrics.grain_groups[0].group == {"ORDER_DATE": "2026-01-01", "REGION": "EMEA"}

    def test_target_aggregate_sql_matches_legacy_aliases(self) -> None:
        """Positional aliases line up across sides even when names differ,
        so the two sides' result rows are read identically."""
        legacy_session = table_session()
        target_session = table_session(region_name="SALES_REGION")
        legacy_metrics = measure(legacy_session, resolved_table(), "legacy")
        target_metrics = measure(target_session, resolved_table(), "target")
        assert sorted(legacy_metrics.columns) == sorted(target_metrics.columns)


class TestCustomSql:
    def test_row_count_only(self) -> None:
        session = RouteSession().add("COUNT(*) AS ROW_COUNT", [{"ROW_COUNT": 7}])
        resolved = resolved_custom_sql("SELECT a, b FROM db.s.t WHERE note = 'a;b'")
        metrics = measure(session, resolved, "legacy")
        assert metrics.object_fqn == "custom-sql"
        assert metrics.row_count == 7
        assert metrics.columns == {}
        assert metrics.distinct_counts == {}
        assert metrics.grain_groups == []
        assert len(session.executed) == 1

    def test_same_sql_verbatim_on_target_side(self) -> None:
        sql = "SELECT a, b FROM db.s.t WHERE note = 'a;b'"
        legacy_session = RouteSession().add("ROW_COUNT", [{"ROW_COUNT": 7}])
        target_session = RouteSession().add("ROW_COUNT", [{"ROW_COUNT": 7}])
        measure(legacy_session, resolved_custom_sql(sql), "legacy")
        measure(target_session, resolved_custom_sql(sql), "target")
        assert legacy_session.executed == target_session.executed
        assert sql in legacy_session.executed[0]

    def test_semicolon_outside_literals_rejected(self) -> None:
        session = RouteSession()
        resolved = resolved_custom_sql("SELECT 1; DROP TABLE db.s.t")
        with pytest.raises(ParityMetricsError, match="semicolon"):
            measure(session, resolved, "legacy")
        assert session.executed == []

    def test_trailing_semicolon_rejected(self) -> None:
        session = RouteSession()
        with pytest.raises(ParityMetricsError, match="semicolon"):
            measure(session, resolved_custom_sql("SELECT 1;"), "legacy")

    def test_semicolon_inside_string_literal_allowed(self) -> None:
        session = RouteSession().add("ROW_COUNT", [{"ROW_COUNT": 1}])
        resolved = resolved_custom_sql(
            "SELECT * FROM db.s.t WHERE note = 'a;b' AND other = 'x''y;z'"
        )
        metrics = measure(session, resolved, "legacy")
        assert metrics.row_count == 1

    def test_semicolon_inside_quoted_identifier_allowed(self) -> None:
        session = RouteSession().add("ROW_COUNT", [{"ROW_COUNT": 1}])
        resolved = resolved_custom_sql('SELECT "odd;name" FROM db.s.t')
        metrics = measure(session, resolved, "legacy")
        assert metrics.row_count == 1

    def test_empty_custom_sql_rejected(self) -> None:
        with pytest.raises(ParityMetricsError, match="no SQL text"):
            measure(RouteSession(), resolved_custom_sql("   "), "legacy")


class TestErrors:
    def test_object_not_found(self) -> None:
        session = RouteSession().add("INFORMATION_SCHEMA.COLUMNS", [])
        with pytest.raises(ParityMetricsError, match=f"object not found: {LEGACY_FQN}"):
            measure(session, resolved_table(), "legacy")

    def test_query_failure_wrapped_with_fqn(self) -> None:
        class BoomSession:
            def execute(self, sql: str, params: Any = None) -> Any:
                raise RuntimeError("warehouse suspended")

        with pytest.raises(ParityMetricsError) as excinfo:
            measure(BoomSession(), resolved_table(), "legacy")
        assert LEGACY_FQN in str(excinfo.value)
        assert "warehouse suspended" in str(excinfo.value)

    def test_refused_relation_not_measurable(self) -> None:
        relation = SourceRelation(
            datasource="ds1", kind="refused", refusal_reason="join"
        )
        resolved = ResolvedObject(relation=relation, target_fqn="")
        with pytest.raises(ParityMetricsError, match="not measurable"):
            measure(RouteSession(), resolved, "legacy")

    def test_non_three_part_fqn_rejected(self) -> None:
        resolved = ResolvedObject(
            relation=table_relation(database=""), target_fqn=TARGET_FQN
        )
        with pytest.raises(ParityMetricsError, match="3-part"):
            measure(RouteSession(), resolved, "legacy")


class TestReadOnlyAndSafety:
    def test_every_generated_statement_passes_read_only_guard(self) -> None:
        sessions = [
            table_session(),
            table_session(region_name="SALES_REGION"),
            RouteSession().add("ROW_COUNT", [{"ROW_COUNT": 1}]),
        ]
        measure(sessions[0], resolved_table(), "legacy")
        measure(sessions[1], resolved_table(), "target")
        measure(
            sessions[2],
            resolved_custom_sql("SELECT a FROM db.s.t WHERE note = 'a;b'"),
            "legacy",
        )
        statements = [sql for session in sessions for sql in session.executed]
        assert len(statements) == 7
        for sql in statements:
            assert_read_only(sql)  # raises ReadOnlyViolation on any non-read

    def test_weird_identifier_quoted_and_escaped(self) -> None:
        session = (
            RouteSession()
            .add(
                "INFORMATION_SCHEMA.COLUMNS",
                [{"COLUMN_NAME": 'Weird "Name', "DATA_TYPE": "NUMBER"}],
            )
            .add("COUNT(*) AS ROW_COUNT", [{"ROW_COUNT": 1, "NULL_0": 0}])
        )
        resolved = resolved_table(column_map={}, keys=(), grain=())
        metrics = measure(session, resolved, "legacy")
        aggregate = session.executed[1]
        assert 'COUNT_IF("WEIRD ""NAME" IS NULL) AS NULL_0' in aggregate
        assert 'SUM("WEIRD ""NAME") AS SUM_0' in aggregate
        assert_read_only(aggregate)
        assert 'WEIRD "NAME' in metrics.columns

    def test_quote_in_table_name_escaped(self) -> None:
        relation = table_relation(table='Bad"Tbl')
        session = (
            RouteSession()
            .add("INFORMATION_SCHEMA.COLUMNS", [{"COLUMN_NAME": "C", "DATA_TYPE": "TEXT"}])
            .add("COUNT(*) AS ROW_COUNT", [{"ROW_COUNT": 0, "NULL_0": 0}])
        )
        resolved = resolved_table(relation=relation, column_map={}, keys=(), grain=())
        measure(session, resolved, "legacy")
        discovery = session.executed[0]
        aggregate = session.executed[1]
        assert "TABLE_NAME = 'BAD\"TBL'" in discovery
        assert '"LEGACY_DB"."SALES"."BAD""TBL"' in aggregate
        for sql in session.executed:
            assert_read_only(sql)

    def test_quote_literal_escapes_single_quotes_in_discovery(self) -> None:
        relation = table_relation(schema="O'BRIEN")
        session = RouteSession().add(
            "INFORMATION_SCHEMA.COLUMNS", [{"COLUMN_NAME": "C", "DATA_TYPE": "TEXT"}]
        )
        session.add("COUNT(*) AS ROW_COUNT", [{"ROW_COUNT": 0, "NULL_0": 0}])
        resolved = resolved_table(relation=relation, column_map={}, keys=(), grain=())
        measure(session, resolved, "legacy")
        assert "TABLE_SCHEMA = 'O''BRIEN'" in session.executed[0]
        assert_read_only(session.executed[0])


class TestDeterminism:
    def test_identical_inputs_produce_byte_identical_sql(self) -> None:
        first = table_session()
        second = table_session()
        measure(first, resolved_table(), "legacy")
        measure(second, resolved_table(), "legacy")
        assert first.executed == second.executed

    def test_target_side_also_deterministic(self) -> None:
        first = table_session(region_name="SALES_REGION")
        second = table_session(region_name="SALES_REGION")
        measure(first, resolved_table(), "target")
        measure(second, resolved_table(), "target")
        assert first.executed == second.executed


class TestMissingDeclaredColumns:
    """QC F3a pinned: a declared key or grain column missing from the
    discovered columns raises ParityMetricsError naming the column(s) and
    the object — never a silent omission that overstates the proof."""

    def test_missing_key_raises_naming_column_and_object(self) -> None:
        session = table_session()
        with pytest.raises(ParityMetricsError) as excinfo:
            measure(session, resolved_table(keys=("ORDER_ID", "NOT_A_COLUMN")), "legacy")
        message = str(excinfo.value)
        assert "NOT_A_COLUMN" in message
        assert LEGACY_FQN in message
        assert "key" in message
        # Discovery ran, but no aggregate was issued on a partial key set.
        assert len(session.executed) == 1

    def test_missing_grain_column_raises_naming_column_and_object(self) -> None:
        session = table_session()
        with pytest.raises(ParityMetricsError) as excinfo:
            measure(session, resolved_table(grain=("ORDER_DATE", "NOT_A_COLUMN")), "legacy")
        message = str(excinfo.value)
        assert "NOT_A_COLUMN" in message
        assert LEGACY_FQN in message
        assert "grain" in message


class TestGrainValueStringification:
    """QC F18: numeric grain values stringify canonically, so the same
    warehouse value compares equal whatever Python type the driver used."""

    def _grain_groups_for(self, value: object) -> dict[str, str]:
        session = (
            RouteSession()
            .add("INFORMATION_SCHEMA.COLUMNS", discovery_rows())
            .add("GROUP BY", [{"G_0": value, "G_1": "EMEA", "GROUP_COUNT": 1}])
            .add("COUNT(*) AS ROW_COUNT", [AGGREGATE_ROW])
        )
        metrics = measure(session, resolved_table(), "legacy")
        return metrics.grain_groups[0].group

    def test_decimal_float_and_int_integral_values_identical(self) -> None:
        from decimal import Decimal

        groups = [self._grain_groups_for(v) for v in (Decimal("5"), 5.0, 5)]
        assert groups[0] == groups[1] == groups[2]
        assert groups[0]["ORDER_DATE"] == "5"

    def test_non_integral_values_stringify_as_float_repr(self) -> None:
        from decimal import Decimal

        assert self._grain_groups_for(Decimal("5.5"))["ORDER_DATE"] == repr(5.5)
        assert self._grain_groups_for(5.5)["ORDER_DATE"] == repr(5.5)

    def test_null_keeps_sentinel(self) -> None:
        assert self._grain_groups_for(None)["ORDER_DATE"] == NULL_GROUP_VALUE

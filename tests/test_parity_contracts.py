"""Contract tests for the migration parity plumbing (PARITY-PLAN S1.1/S1.2).

These pin the seams the parity family hangs off: the new CheckFamily value,
the parity target kind, extras passthrough in the runner, the metrics
record codec, snapshot naming, and that every fixture loads through the
single workbook parser.
"""

from __future__ import annotations

import pytest

from plumb.checks._tableau import TableauParseError, parse_workbook
from plumb.config.models import Ruleset
from plumb.engine.models import CheckFamily, Target
from plumb.engine.runner import RunRequest, run_checks
from plumb.parity.contracts import (
    EXTRAS_KEY,
    ColumnMetrics,
    GrainGroup,
    ParityBundle,
    ParityMetrics,
    SourceRelation,
    snapshot_name,
    snapshot_prefix_for,
)
from tests._parity_fixtures import (
    TWB_CUSTOM_SQL,
    TWB_EXTRACT_ONLY,
    TWB_EXTRACT_OVER_LIVE,
    TWB_JOIN,
    TWB_MALFORMED,
    TWB_TWO_TABLES,
    write_twb,
)


def _minimal_ruleset() -> Ruleset:
    return Ruleset.model_validate({"version": "test", "checks": []})


class TestEnginePlumbing:
    def test_migration_parity_family_exists(self):
        assert CheckFamily.MIGRATION_PARITY.value == "migration_parity"

    def test_parity_target_validates(self):
        target = Target(type="parity", name="sales.twbx")
        assert target.type == "parity"

    def test_unknown_target_type_still_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Target(type="nonsense", name="x")

    def test_runner_passes_extras_through(self):
        bundle = ParityBundle(mode="check", workbook_path="wb.twbx")
        result = run_checks(
            RunRequest(
                target=Target(type="parity", name="wb.twbx"),
                ruleset=_minimal_ruleset(),
                extras={EXTRAS_KEY: bundle},
            )
        )
        # No parity checks enabled in the minimal ruleset: an empty, READY run
        # proves routing does not crash and extras are accepted.
        assert result.target.type == "parity"
        assert result.checks == []

    def test_sql_target_unaffected(self):
        result = run_checks(
            RunRequest(
                target=Target(type="sql", name="q.sql"),
                ruleset=_minimal_ruleset(),
                sql_text="SELECT 1",
            )
        )
        assert result.target.type == "sql"


class TestMetricsCodec:
    def _metrics(self) -> ParityMetrics:
        return ParityMetrics(
            object_fqn="LEGACY_DB.SALES.ORDERS",
            row_count=12345,
            columns={
                "SALES": ColumnMetrics(
                    data_type="NUMBER",
                    null_count=3,
                    sum_value=987654.25,
                    min_value=-10.5,
                    max_value=5000.0,
                ),
                "REGION": ColumnMetrics(data_type="TEXT", null_count=0),
            },
            distinct_counts={"ORDER_ID": 12345},
            grain_groups=[
                GrainGroup(group={"REGION": "EMEA"}, count=500),
                GrainGroup(group={"REGION": "APAC"}, count=700),
            ],
        )

    def test_round_trip_lossless(self):
        original = self._metrics()
        restored = ParityMetrics.from_records(original.to_records())
        assert restored == original

    def test_non_numeric_column_has_no_aggregates(self):
        restored = ParityMetrics.from_records(self._metrics().to_records())
        region = restored.columns["REGION"]
        assert region.sum_value is None
        assert region.min_value is None
        assert region.max_value is None

    def test_records_are_flat_and_typed(self):
        for rec in self._metrics().to_records():
            assert set(rec) == {"kind", "column", "value", "text"}
            assert rec["value"] is None or isinstance(rec["value"], float)


class TestSnapshotNaming:
    def test_prefix_is_sanitized(self):
        assert snapshot_prefix_for("C:/x/Q1 Sales (Final).twbx") == "parity__q1-sales-final"

    def test_name_is_flat_and_safe(self):
        rel = SourceRelation(
            datasource="Orders (Legacy)",
            kind="table",
            database="LEGACY_DB",
            schema="SALES",
            table="ORDERS",
        )
        name = snapshot_name("parity__wb", rel)
        assert name == "parity__wb__orders-legacy__legacy_db-sales-orders"
        assert "/" not in name and "\\" not in name

    def test_custom_sql_names_are_stable(self):
        a = SourceRelation(datasource="ds", kind="custom_sql", custom_sql="SELECT 1")
        b = SourceRelation(datasource="ds", kind="custom_sql", custom_sql="SELECT 1")
        c = SourceRelation(datasource="ds", kind="custom_sql", custom_sql="SELECT 2")
        assert snapshot_name("p", a) == snapshot_name("p", b)
        assert snapshot_name("p", a) != snapshot_name("p", c)


class TestRelationFqn:
    def test_table_fqn(self):
        rel = SourceRelation(
            datasource="ds", kind="table", database="DB", schema="S", table="T"
        )
        assert rel.fqn == "DB.S.T"

    def test_custom_sql_has_no_fqn(self):
        rel = SourceRelation(datasource="ds", kind="custom_sql", custom_sql="SELECT 1")
        assert rel.fqn is None


class TestFixturesLoad:
    @pytest.mark.parametrize(
        "content,n_datasources",
        [
            (TWB_TWO_TABLES, 2),
            (TWB_CUSTOM_SQL, 1),
            (TWB_JOIN, 1),
            (TWB_EXTRACT_ONLY, 1),
            (TWB_EXTRACT_OVER_LIVE, 1),
        ],
    )
    def test_fixture_parses(self, tmp_path, content, n_datasources):
        wb = parse_workbook(write_twb(tmp_path, content))
        assert len(wb.datasources) == n_datasources

    def test_custom_sql_fixture_carries_sql(self, tmp_path):
        wb = parse_workbook(write_twb(tmp_path, TWB_CUSTOM_SQL))
        assert any("SUM(SALES)" in sql for sql in wb.datasources[0].custom_sql)

    def test_extract_fixtures_flagged(self, tmp_path):
        only = parse_workbook(write_twb(tmp_path, TWB_EXTRACT_ONLY))
        over = parse_workbook(write_twb(tmp_path, TWB_EXTRACT_OVER_LIVE))
        assert only.datasources[0].has_extract
        assert over.datasources[0].has_extract

    def test_malformed_raises_parse_error(self, tmp_path):
        with pytest.raises(TableauParseError):
            parse_workbook(write_twb(tmp_path, TWB_MALFORMED))

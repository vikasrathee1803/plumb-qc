"""Integration tests for the parity pipeline (PARITY-PLAN S3.2).

End to end through run_parity with RouteSession fakes and a real
LocalParquetStore in tmp_path: snapshot writes baselines, check loads and
compares them, drift fails M-ROW-001, measurement failures surface in the
verdict (never silently), and static-only runs skip honestly. Also pins
the M-SNAP-001 snapshot-phase completeness behavior added by the lead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plumb.baseline.store import LocalParquetStore
from plumb.config.models import Ruleset
from plumb.engine.models import RunResult, Status, Verdict
from plumb.parity.runner import run_parity
from tests._fakes import RouteSession, callable_session
from tests._parity_fixtures import TWB_CUSTOM_SQL, TWB_JOIN, TWB_TWO_TABLES, write_twb

M_IDS = [
    "M-SRC-001",
    "M-MAP-001",
    "M-SNAP-001",
    "M-SCHEMA-001",
    "M-ROW-001",
    "M-AGG-001",
    "M-NULL-001",
    "M-DIST-001",
    "M-GRAIN-001",
]


def parity_ruleset() -> Ruleset:
    return Ruleset.model_validate(
        {"version": "test", "checks": [{"id": cid, "enabled": True} for cid in M_IDS]}
    )


def by_id(result: RunResult, check_id: str) -> Status:
    for check in result.checks:
        if check.id == check_id:
            return check.status
    raise AssertionError(f"{check_id} missing from results")


def table_session(orders_rows: int, customers_rows: int) -> RouteSession:
    """Routes for the TWO_TABLES fixture: discovery first (matched by the
    INFORMATION_SCHEMA literal), then per-table aggregates (matched by the
    quoted FQN, which only the aggregate/grain SQL contains)."""
    return RouteSession(
        routes=[
            ("TABLE_NAME = 'ORDERS'", [{"COLUMN_NAME": "SALES", "DATA_TYPE": "NUMBER"}]),
            (
                "TABLE_NAME = 'CUSTOMERS'",
                [{"COLUMN_NAME": "CUSTOMER_ID", "DATA_TYPE": "TEXT"}],
            ),
            (
                '"LEGACY_DB"."SALES"."ORDERS"',
                [
                    {
                        "ROW_COUNT": orders_rows,
                        "NULL_0": 2,
                        "SUM_0": 500.0,
                        "MIN_0": 1.0,
                        "MAX_0": 9.0,
                    }
                ],
            ),
            (
                '"LEGACY_DB"."CRM"."CUSTOMERS"',
                [{"ROW_COUNT": customers_rows, "NULL_0": 0}],
            ),
        ]
    )


class TestSnapshotPhase:
    def test_custom_sql_snapshot_writes_baseline(self, tmp_path: Path):
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        store = LocalParquetStore(tmp_path / "store")
        session = RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": 42}])])
        result = run_parity(
            workbook=wb, mode="snapshot", ruleset=parity_ruleset(), store=store, session=session
        )
        assert by_id(result, "M-SRC-001") is Status.PASS
        assert by_id(result, "M-MAP-001") is Status.PASS
        assert by_id(result, "M-SNAP-001") is Status.PASS
        assert by_id(result, "M-ROW-001") is Status.SKIP  # snapshot phase
        names = store.list_names()
        assert len(names) == 1 and names[0].startswith("parity__kpi__")

    def test_table_snapshot_writes_both_relations(self, tmp_path: Path):
        wb = write_twb(tmp_path, TWB_TWO_TABLES, "sales.twb")
        store = LocalParquetStore(tmp_path / "store")
        result = run_parity(
            workbook=wb,
            mode="snapshot",
            ruleset=parity_ruleset(),
            store=store,
            session=table_session(100, 10),
        )
        assert by_id(result, "M-SNAP-001") is Status.PASS
        assert len(store.list_names()) == 2

    def test_measurement_failure_is_verdict_visible(self, tmp_path: Path):
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        store = LocalParquetStore(tmp_path / "store")

        def explode(sql: str) -> list[dict[str, object]]:
            raise RuntimeError("warehouse suspended")

        result = run_parity(
            workbook=wb,
            mode="snapshot",
            ruleset=parity_ruleset(),
            store=store,
            session=callable_session(explode),
        )
        assert by_id(result, "M-SNAP-001") is Status.ERROR
        assert result.verdict in (Verdict.BLOCKED, Verdict.REVIEW)
        assert store.list_names() == []

    def test_static_only_snapshot_skips(self, tmp_path: Path):
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        store = LocalParquetStore(tmp_path / "store")
        result = run_parity(
            workbook=wb, mode="snapshot", ruleset=parity_ruleset(), store=store, session=None
        )
        assert by_id(result, "M-SRC-001") is Status.PASS
        assert by_id(result, "M-SNAP-001") is Status.SKIP

    def test_store_save_value_error_is_verdict_visible(self, tmp_path: Path):
        """QC F8: pyarrow failures (ValueError/TypeError subclasses) during
        store.save must land in bundle.errors -> M-SNAP-001 ERROR."""

        class ExplodingStore(LocalParquetStore):
            def save(self, baseline: object) -> None:
                raise ValueError("ArrowInvalid: could not convert")

        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        result = run_parity(
            workbook=wb,
            mode="snapshot",
            ruleset=parity_ruleset(),
            store=ExplodingStore(tmp_path / "store"),
            session=RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": 42}])]),
        )
        assert by_id(result, "M-SNAP-001") is Status.ERROR
        assert result.verdict in (Verdict.BLOCKED, Verdict.REVIEW)

    def test_refused_relations_reach_coverage_as_m_src_002(self, tmp_path: Path):
        """QC F6: a refused join lands in coverage.checks_skipped with its
        reason; M-SRC-001 stays the verdict-bearing result."""
        wb = write_twb(tmp_path, TWB_JOIN, "joined.twb")
        store = LocalParquetStore(tmp_path / "store")
        result = run_parity(
            workbook=wb, mode="snapshot", ruleset=parity_ruleset(), store=store, session=None
        )
        skipped = {c.id: c for c in result.coverage.checks_skipped}
        assert "M-SRC-002" in skipped
        assert "join" in skipped["M-SRC-002"].reason
        assert "Orders + Customers (Join)" in skipped["M-SRC-002"].reason


class TestCheckPhase:
    def _snapshot_then_check(
        self, tmp_path: Path, snapshot_rows: int, check_rows: int
    ) -> RunResult:
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        store = LocalParquetStore(tmp_path / "store")
        run_parity(
            workbook=wb,
            mode="snapshot",
            ruleset=parity_ruleset(),
            store=store,
            session=RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": snapshot_rows}])]),
        )
        return run_parity(
            workbook=wb,
            mode="check",
            ruleset=parity_ruleset(),
            store=store,
            session=RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": check_rows}])]),
        )

    def test_parity_passes_when_counts_match(self, tmp_path: Path):
        result = self._snapshot_then_check(tmp_path, 42, 42)
        assert by_id(result, "M-SNAP-001") is Status.PASS
        assert by_id(result, "M-ROW-001") is Status.PASS

    def test_row_drift_blocks(self, tmp_path: Path):
        result = self._snapshot_then_check(tmp_path, 42, 50)
        assert by_id(result, "M-ROW-001") is Status.FAIL
        assert result.verdict is Verdict.BLOCKED

    def test_corrupt_snapshot_fails_loud_not_traceback(self, tmp_path: Path):
        """QC F8: a truncated/corrupt snapshot parquet surfaces as a named
        M-SNAP-001 FAIL through bundle.errors, never an exception."""
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        store = LocalParquetStore(tmp_path / "store")
        run_parity(
            workbook=wb,
            mode="snapshot",
            ruleset=parity_ruleset(),
            store=store,
            session=RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": 42}])]),
        )
        parquet_files = list((tmp_path / "store").glob("*.parquet"))
        assert len(parquet_files) == 1
        parquet_files[0].write_bytes(b"this is not parquet")
        result = run_parity(
            workbook=wb,
            mode="check",
            ruleset=parity_ruleset(),
            store=store,
            session=RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": 42}])]),
        )
        check = next(c for c in result.checks if c.id == "M-SNAP-001")
        assert check.status is Status.FAIL
        assert "snapshot unreadable" in (check.observed or "")
        assert result.verdict is Verdict.BLOCKED

    def test_pre_codec_snapshot_rejected_loud(self, tmp_path: Path):
        """QC F11: a snapshot written without the codec record (an older
        build) is refused with a named error, never decoded silently."""
        from plumb.baseline.store import Baseline
        from plumb.parity.contracts import RECORD_COLUMNS

        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        store = LocalParquetStore(tmp_path / "store")
        run_parity(
            workbook=wb,
            mode="snapshot",
            ruleset=parity_ruleset(),
            store=store,
            session=RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": 42}])]),
        )
        name = store.list_names()[0]
        legacy_rows = [
            {"kind": "object_fqn", "column": None, "value": None, "text": "custom-sql"},
            {"kind": "row_count", "column": None, "value": 42.0, "text": None},
        ]
        store.save(
            Baseline(
                name=name,
                columns=list(RECORD_COLUMNS),
                rows=legacy_rows,
                row_count=len(legacy_rows),
            )
        )
        result = run_parity(
            workbook=wb,
            mode="check",
            ruleset=parity_ruleset(),
            store=store,
            session=RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": 42}])]),
        )
        check = next(c for c in result.checks if c.id == "M-SNAP-001")
        assert check.status is Status.FAIL
        assert "codec" in (check.observed or "")

    def test_check_without_snapshot_blocks(self, tmp_path: Path):
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        store = LocalParquetStore(tmp_path / "store")
        result = run_parity(
            workbook=wb,
            mode="check",
            ruleset=parity_ruleset(),
            store=store,
            session=RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": 42}])]),
        )
        assert by_id(result, "M-SNAP-001") is Status.FAIL
        assert result.verdict is Verdict.BLOCKED
        # No snapshot -> the target side is never queried (no wasted reads).
        assert result.summary.total > 0

    def test_static_only_check_skips_values_honestly(self, tmp_path: Path):
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        store = LocalParquetStore(tmp_path / "store")
        run_parity(
            workbook=wb,
            mode="snapshot",
            ruleset=parity_ruleset(),
            store=store,
            session=RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": 42}])]),
        )
        result = run_parity(
            workbook=wb, mode="check", ruleset=parity_ruleset(), store=store, session=None
        )
        assert by_id(result, "M-SNAP-001") is Status.PASS  # static, store-backed
        assert by_id(result, "M-ROW-001") is Status.SKIP
        skipped_ids = {c.id for c in result.coverage.checks_skipped}
        assert "M-ROW-001" in skipped_ids

    def test_table_workbook_full_round_trip(self, tmp_path: Path):
        wb = write_twb(tmp_path, TWB_TWO_TABLES, "sales.twb")
        store = LocalParquetStore(tmp_path / "store")
        run_parity(
            workbook=wb,
            mode="snapshot",
            ruleset=parity_ruleset(),
            store=store,
            session=table_session(100, 10),
        )
        result = run_parity(
            workbook=wb,
            mode="check",
            ruleset=parity_ruleset(),
            store=store,
            session=table_session(100, 10),
        )
        assert by_id(result, "M-SCHEMA-001") is Status.PASS
        assert by_id(result, "M-ROW-001") is Status.PASS
        assert by_id(result, "M-AGG-001") is Status.PASS
        assert by_id(result, "M-NULL-001") is Status.PASS
        assert result.verdict in (Verdict.READY, Verdict.READY_WITH_NOTES)

    def test_table_aggregate_drift_fails_agg(self, tmp_path: Path):
        wb = write_twb(tmp_path, TWB_TWO_TABLES, "sales.twb")
        store = LocalParquetStore(tmp_path / "store")
        run_parity(
            workbook=wb,
            mode="snapshot",
            ruleset=parity_ruleset(),
            store=store,
            session=table_session(100, 10),
        )
        drifted = table_session(100, 10)
        drifted.routes[2] = (
            '"LEGACY_DB"."SALES"."ORDERS"',
            [{"ROW_COUNT": 100, "NULL_0": 2, "SUM_0": 999.0, "MIN_0": 1.0, "MAX_0": 9.0}],
        )
        result = run_parity(
            workbook=wb, mode="check", ruleset=parity_ruleset(), store=store, session=drifted
        )
        assert by_id(result, "M-ROW-001") is Status.PASS
        assert by_id(result, "M-AGG-001") is Status.FAIL


class TestBadInputs:
    def test_unreadable_workbook_raises_before_any_query(self, tmp_path: Path):
        from plumb.checks._tableau import TableauParseError
        from tests._parity_fixtures import TWB_MALFORMED

        wb = write_twb(tmp_path, TWB_MALFORMED, "bad.twb")
        store = LocalParquetStore(tmp_path / "store")
        session = RouteSession()
        with pytest.raises(TableauParseError):
            run_parity(
                workbook=wb, mode="snapshot", ruleset=parity_ruleset(), store=store, session=session
            )
        assert session.executed == []

    def test_bad_map_raises_config_error(self, tmp_path: Path):
        from plumb.config.loader import ConfigError

        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        bad_map = tmp_path / "map.yml"
        bad_map.write_text("version: 1\nnonsense_key: true\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            run_parity(
                workbook=wb,
                mode="snapshot",
                ruleset=parity_ruleset(),
                store=LocalParquetStore(tmp_path / "store"),
                map_path=bad_map,
            )

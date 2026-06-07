"""Stream C regression family fixture tests. The confidence centerpiece:
unchanged output passes, changed output reports the moved rows."""

from pathlib import Path

from plumb.baseline.store import LocalParquetStore, make_baseline
from plumb.checks.sql_regression import r_agg_001, r_diff_001
from plumb.engine.models import Status
from tests._fakes import RouteSession, make_ctx

TARGET = "SELECT region, amount FROM rpt_sales"
ROWS = [
    {"REGION": "EAST", "AMOUNT": 100},
    {"REGION": "WEST", "AMOUNT": 200},
]


def _store_with_baseline(tmp_path: Path, rows) -> LocalParquetStore:
    store = LocalParquetStore(tmp_path)
    columns = list(rows[0].keys()) if rows else []
    store.save(make_baseline("sales_daily", columns, rows))
    return store


def test_no_baseline_skips_with_clear_reason(tmp_path: Path):
    store = LocalParquetStore(tmp_path)
    session = RouteSession().add("__plumb_target", ROWS)
    res = r_diff_001(
        make_ctx(TARGET, session=session, baseline_store=store, baseline_name="missing"), {}
    )
    assert res.status is Status.SKIP
    assert res.observed == "no baseline found"


def test_unchanged_output_passes(tmp_path: Path):
    store = _store_with_baseline(tmp_path, ROWS)
    session = RouteSession().add("FROM __plumb_target", ROWS)
    res = r_diff_001(
        make_ctx(TARGET, session=session, baseline_store=store, baseline_name="sales_daily"), {}
    )
    assert res.status is Status.PASS


def test_changed_rows_fail_and_report_moves(tmp_path: Path):
    store = _store_with_baseline(tmp_path, ROWS)
    changed = [
        {"REGION": "EAST", "AMOUNT": 100},
        {"REGION": "WEST", "AMOUNT": 999},  # changed value
    ]
    session = RouteSession().add("FROM __plumb_target", changed)
    res = r_diff_001(
        make_ctx(TARGET, session=session, baseline_store=store, baseline_name="sales_daily"), {}
    )
    assert res.status is Status.FAIL
    assert "added" in (res.observed or "") and "removed" in (res.observed or "")
    changes = {r["__plumb_change"] for r in res.evidence.sample_rows}
    assert changes == {"added", "removed"}


def test_schema_drift_fails(tmp_path: Path):
    store = _store_with_baseline(tmp_path, ROWS)
    drifted = [{"REGION": "EAST", "AMOUNT": 100, "NEW_COL": 1}]
    session = RouteSession().add("FROM __plumb_target", drifted)
    res = r_diff_001(
        make_ctx(TARGET, session=session, baseline_store=store, baseline_name="sales_daily"), {}
    )
    assert res.status is Status.FAIL
    assert "schema changed" in (res.observed or "")


def test_aggregate_fingerprint_detects_sum_drift(tmp_path: Path):
    store = _store_with_baseline(tmp_path, ROWS)
    drifted = [{"REGION": "EAST", "AMOUNT": 100}, {"REGION": "WEST", "AMOUNT": 300}]
    session = RouteSession().add("FROM __plumb_target", drifted)
    res = r_agg_001(
        make_ctx(TARGET, session=session, baseline_store=store, baseline_name="sales_daily"), {}
    )
    assert res.status is Status.FAIL
    assert "sum:AMOUNT" in (res.observed or "")

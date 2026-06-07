"""Stream B assertions family fixture tests. Covers the acceptance-critical
grain fan-out and reconciliation drift behaviors."""

from datetime import datetime, timedelta, timezone

from plumb.checks.sql_assertions import (
    d_dup_001,
    d_fresh_001,
    d_grain_001,
    d_null_001,
    d_recon_001,
)
from plumb.engine.models import Severity, Status
from tests._fakes import RouteSession, make_ctx

TARGET = "SELECT o.order_id, c.name FROM orders o JOIN dim_customer c ON o.cust_id = c.id"


def test_grain_fanout_is_blocked_and_names_key():
    session = RouteSession().add(
        "__PLUMB_DUP_COUNT",
        [
            {"ORDER_ID": 1, "__PLUMB_DUP_COUNT": 4},
            {"ORDER_ID": 2, "__PLUMB_DUP_COUNT": 2},
        ],
    )
    res = d_grain_001(make_ctx(TARGET, session=session), {"key": ["order_id"]})
    assert res.status is Status.FAIL
    assert res.severity is Severity.BLOCKER
    assert "order_id" in (res.expected or "")
    assert "max duplication 4x" in (res.observed or "")
    assert res.evidence.sample_rows  # the duplicated keys are shown


def test_grain_unique_passes():
    session = RouteSession().add("__PLUMB_DUP_COUNT", [])
    res = d_grain_001(make_ctx(TARGET, session=session), {"key": ["order_id"]})
    assert res.status is Status.PASS


def test_grain_without_key_skips_not_guesses():
    session = RouteSession()
    res = d_grain_001(make_ctx(TARGET, session=session), {})
    assert res.status is Status.SKIP


def test_recon_breach_is_blocked_with_observed_vs_expected():
    session = RouteSession()
    session.add("SUM(amount)", [{"M": 1000.0}])  # metric over the target subquery
    session.add("FCT_SALES", [{"M": 900.0}])  # source of truth
    params = {
        "metric_sql": "SELECT SUM(amount) AS M FROM {{ target }}",
        "source_of_truth_sql": "SELECT SUM(net_amount) AS M FROM ANALYTICS.MART.FCT_SALES",
        "tolerance_abs": 0,
        "tolerance_pct": 0.001,
    }
    res = d_recon_001(make_ctx(TARGET, session=session), params)
    assert res.status is Status.FAIL
    assert res.severity is Severity.BLOCKER
    assert "1000" in (res.observed or "") and "900" in (res.observed or "")
    assert "difference" in (res.observed or "")


def test_recon_within_tolerance_passes():
    session = RouteSession()
    session.add("SUM(amount)", [{"M": 1000.0}])
    session.add("FCT_SALES", [{"M": 1000.0}])
    params = {
        "metric_sql": "SELECT SUM(amount) AS M FROM {{ target }}",
        "source_of_truth_sql": "SELECT SUM(net_amount) AS M FROM ANALYTICS.MART.FCT_SALES",
        "tolerance_abs": 0,
        "tolerance_pct": 0.0,
    }
    res = d_recon_001(make_ctx(TARGET, session=session), params)
    assert res.status is Status.PASS


def test_null_key_is_blocker_fail():
    session = RouteSession().add(
        "__PLUMB_TOTAL", [{"__PLUMB_TOTAL": 100, "__PLUMB_NULLS_ORDER_ID": 3}]
    )
    res = d_null_001(make_ctx(TARGET, session=session), {"key": ["order_id"]})
    assert res.status is Status.FAIL
    assert res.severity is Severity.BLOCKER


def test_freshness_stale_fails():
    old = datetime(2026, 6, 1, tzinfo=timezone.utc)
    now = old + timedelta(hours=48)
    session = RouteSession().add(
        "__PLUMB_MAX_TS", [{"__PLUMB_MAX_TS": old, "__PLUMB_NOW": now}]
    )
    res = d_fresh_001(
        make_ctx(TARGET, session=session), {"event_ts_col": "created_at", "sla_hours": 6}
    )
    assert res.status is Status.FAIL


def test_freshness_handles_date_column_vs_tzaware_now():
    """Regression: a DATE column returns datetime.date, and CURRENT_TIMESTAMP
    returns a tz-aware datetime. Age must still compute, not WARN. Found on
    the live native connector run."""
    from datetime import date, datetime, timezone

    old = date(1998, 8, 2)
    now = datetime(2026, 6, 7, 12, tzinfo=timezone.utc)
    session = RouteSession().add(
        "__PLUMB_MAX_TS", [{"__PLUMB_MAX_TS": old, "__PLUMB_NOW": now}]
    )
    res = d_fresh_001(
        make_ctx(TARGET, session=session), {"event_ts_col": "d", "sla_hours": 24}
    )
    assert res.status is Status.FAIL
    assert "old" in (res.observed or "")


def test_freshness_within_sla_passes():
    now = datetime(2026, 6, 7, tzinfo=timezone.utc)
    recent = now - timedelta(hours=2)
    session = RouteSession().add(
        "__PLUMB_MAX_TS", [{"__PLUMB_MAX_TS": recent, "__PLUMB_NOW": now}]
    )
    res = d_fresh_001(
        make_ctx(TARGET, session=session), {"event_ts_col": "created_at", "sla_hours": 6}
    )
    assert res.status is Status.PASS


def test_full_row_duplicates_fail():
    session = RouteSession().add("__PLUMB_DUP_ROWS", [{"__PLUMB_DUP_ROWS": 5}])
    res = d_dup_001(make_ctx(TARGET, session=session), {})
    assert res.status is Status.FAIL

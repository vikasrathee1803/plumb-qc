"""Stream D performance family fixture tests with fake EXPLAIN output."""

from plumb.checks.sql_performance import (
    p_card_001,
    p_cost_001,
    p_prof_001,
    p_spill_001,
)
from plumb.engine.models import Status
from tests._fakes import RouteSession, make_ctx

TARGET = "SELECT a FROM big_fact"


def test_full_scan_smell_warns():
    session = RouteSession().add(
        "EXPLAIN",
        [{"operation": "TableScan", "partitionsTotal": 5000, "partitionsAssigned": 5000}],
    )
    res = p_prof_001(make_ctx(TARGET, session=session), {})
    assert res.status is Status.WARN


def test_good_pruning_passes():
    session = RouteSession().add(
        "EXPLAIN",
        [{"operation": "TableScan", "partitionsTotal": 5000, "partitionsAssigned": 12}],
    )
    res = p_prof_001(make_ctx(TARGET, session=session), {})
    assert res.status is Status.PASS


def test_cost_over_budget_warns():
    session = RouteSession().add(
        "EXPLAIN", [{"operation": "TableScan", "partitionsAssigned": 5000}]
    )
    res = p_cost_001(make_ctx(TARGET, session=session), {"max_partitions": 100})
    assert res.status is Status.WARN


def test_cost_without_threshold_skips():
    session = RouteSession().add("EXPLAIN", [{"partitionsAssigned": 5000}])
    res = p_cost_001(make_ctx(TARGET, session=session), {})
    assert res.status is Status.SKIP


def test_spillage_skips_honestly_without_runtime_profile():
    res = p_spill_001(make_ctx(TARGET, session=RouteSession()), {})
    assert res.status is Status.SKIP
    assert "runtime query profile" in (res.observed or "")


def test_cardinality_explosion_warns():
    session = RouteSession().add(
        "EXPLAIN",
        [
            {"operation": "TableScan", "rows": 100},
            {"operation": "Join", "rows": 5000},
        ],
    )
    res = p_card_001(make_ctx(TARGET, session=session), {"blow_up_factor": 10})
    assert res.status is Status.WARN

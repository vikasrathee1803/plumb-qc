"""Fixture tests for the checks added beyond the spec catalog."""

from plumb.checks.sql_assertions import d_blank_001, d_distinct_001, d_pos_001
from plumb.checks.sql_static import s_stat_011
from plumb.engine.models import Severity, Status
from tests._fakes import RouteSession, make_ctx

TARGET = "SELECT id, name, amount FROM db.s.t"


class TestNestedOrderBy:
    def test_order_by_in_subquery_warns(self):
        sql = "SELECT * FROM (SELECT id FROM t ORDER BY id) x"
        assert s_stat_011(make_ctx(sql), {}).status is Status.WARN

    def test_order_by_in_cte_warns(self):
        sql = "WITH c AS (SELECT id FROM t ORDER BY id) SELECT * FROM c"
        assert s_stat_011(make_ctx(sql), {}).status is Status.WARN

    def test_top_level_order_by_is_fine(self):
        assert s_stat_011(make_ctx("SELECT id FROM t ORDER BY id"), {}).status is Status.PASS

    def test_subquery_order_with_limit_is_fine(self):
        sql = "SELECT * FROM (SELECT id FROM t ORDER BY id LIMIT 10) x"
        assert s_stat_011(make_ctx(sql), {}).status is Status.PASS

    def test_window_order_is_not_flagged(self):
        sql = "SELECT id, ROW_NUMBER() OVER (ORDER BY ts) AS rn FROM t"
        assert s_stat_011(make_ctx(sql), {}).status is Status.PASS


class TestBlankRate:
    def test_blank_over_threshold_fails(self):
        session = RouteSession().add(
            "__PLUMB_TOTAL", [{"__PLUMB_TOTAL": 100, "__PLUMB_BLANK_NAME": 12}]
        )
        params = {"columns": ["name"], "threshold": 0.05}
        res = d_blank_001(make_ctx(TARGET, session=session), params)
        assert res.status is Status.FAIL
        assert "0.12" in (res.observed or "")

    def test_blank_within_threshold_passes(self):
        session = RouteSession().add(
            "__PLUMB_TOTAL", [{"__PLUMB_TOTAL": 100, "__PLUMB_BLANK_NAME": 1}]
        )
        params = {"columns": ["name"], "threshold": 0.05}
        res = d_blank_001(make_ctx(TARGET, session=session), params)
        assert res.status is Status.PASS

    def test_no_columns_skips(self):
        assert d_blank_001(make_ctx(TARGET, session=RouteSession()), {}).status is Status.SKIP


class TestNonNegative:
    def test_negative_values_fail_high(self):
        session = RouteSession().add("__PLUMB_NEG_AMOUNT", [{"__PLUMB_NEG_AMOUNT": 3}])
        res = d_pos_001(make_ctx(TARGET, session=session), {"columns": ["amount"]})
        assert res.status is Status.FAIL
        assert res.severity is Severity.HIGH

    def test_no_negatives_pass(self):
        session = RouteSession().add("__PLUMB_NEG_AMOUNT", [{"__PLUMB_NEG_AMOUNT": 0}])
        res = d_pos_001(make_ctx(TARGET, session=session), {"columns": ["amount"]})
        assert res.status is Status.PASS


class TestDistinctBounds:
    def test_distinct_out_of_bounds_fails(self):
        session = RouteSession().add("__PLUMB_DISTINCT", [{"__PLUMB_DISTINCT": 2}])
        res = d_distinct_001(
            make_ctx(TARGET, session=session), {"column": "id", "min": 10, "max": 1000}
        )
        assert res.status is Status.FAIL
        assert "2 distinct" in (res.observed or "")

    def test_distinct_within_bounds_passes(self):
        session = RouteSession().add("__PLUMB_DISTINCT", [{"__PLUMB_DISTINCT": 50}])
        res = d_distinct_001(
            make_ctx(TARGET, session=session), {"column": "id", "min": 10, "max": 1000}
        )
        assert res.status is Status.PASS

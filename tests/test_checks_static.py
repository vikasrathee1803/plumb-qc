"""Stream A static family: at least one fixture-backed test per check."""

from plumb.checks.sql_static import (
    s_stat_001,
    s_stat_002,
    s_stat_003,
    s_stat_010,
)
from plumb.engine.models import Severity, Status
from tests._fakes import make_ctx


def test_select_star_fails_high():
    res = s_stat_001(make_ctx("SELECT * FROM db.sch.t"), {})
    assert res.status is Status.FAIL
    assert res.severity is Severity.HIGH


def test_explicit_columns_pass():
    res = s_stat_001(make_ctx("SELECT a, b FROM db.sch.t"), {})
    assert res.status is Status.PASS


def test_cartesian_join_is_blocker_fail():
    res = s_stat_002(make_ctx("SELECT a FROM t, u"), {})
    assert res.status is Status.FAIL
    assert res.severity is Severity.BLOCKER


def test_join_with_condition_passes():
    res = s_stat_002(make_ctx("SELECT a FROM t JOIN u ON t.id = u.id"), {})
    assert res.status is Status.PASS


def test_not_in_subquery_fails():
    res = s_stat_003(make_ctx("SELECT a FROM t WHERE a NOT IN (SELECT b FROM u)"), {})
    assert res.status is Status.FAIL


def test_not_in_literal_list_is_fine():
    res = s_stat_003(make_ctx("SELECT a FROM t WHERE a NOT IN (1, 2, 3)"), {})
    assert res.status is Status.PASS


def test_distinct_over_join_is_heuristic_warn():
    res = s_stat_010(
        make_ctx("SELECT DISTINCT a FROM t JOIN u ON t.id = u.id"), {}
    )
    assert res.status is Status.WARN


def test_unparseable_sql_is_surfaced_as_error():
    res = s_stat_001(make_ctx("SELEKT FROM WHERE )("), {})
    assert res.status is Status.ERROR

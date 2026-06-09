"""Stream A static family: at least one fixture-backed test per check."""

from plumb.checks.sql_static import (
    s_stat_001,
    s_stat_002,
    s_stat_003,
    s_stat_010,
    s_stat_012,
    s_stat_013,
    s_stat_014,
    s_stat_015,
)
from plumb.engine.models import Severity, Status
from tests._fakes import make_ctx


def test_integration_layer_read_flags_when_configured():
    from plumb.config.models import Ruleset

    rs = Ruleset(version="1", integration_layer_patterns=["IL"])
    ctx = make_ctx("SELECT id FROM X_PRD_IL.SALES.ORDERS", ruleset=rs)
    assert s_stat_015(ctx, {}).status is Status.FAIL
    # off by default (no patterns) so other teams are not surprised
    assert s_stat_015(make_ctx("SELECT id FROM X_PRD_IL.SALES.ORDERS"), {}).status is Status.SKIP


def test_raw_layer_is_a_blocker_in_the_shipped_ruleset():
    from pathlib import Path

    from plumb.config.loader import load_ruleset

    rs = load_ruleset(Path("rules/plumb.yml"), enforce_pin=False)
    res = s_stat_013(make_ctx("SELECT id FROM X_PRD_RL.SALES.ORDERS", ruleset=rs), {})
    assert res.status is Status.FAIL and res.severity is Severity.BLOCKER


def test_sandbox_reference_fails_high():
    sql = "SELECT * FROM SANDBOX_VIKAS.WORK.ORDERS o JOIN PROD.MART.D d ON o.id = d.id"
    res = s_stat_012(make_ctx(sql), {})
    assert res.status is Status.FAIL and res.severity is Severity.HIGH
    assert "SANDBOX" in (res.observed or "")


def test_sandbox_does_not_false_positive_on_device():
    res = s_stat_012(make_ctx("SELECT * FROM PROD.MART.DEVICE_EVENTS"), {})
    assert res.status is Status.PASS  # DEVICE must not match the DEV token


def test_layer_match_ignores_the_table_name():
    # the layer is a database/schema convention; a token in the table name
    # (a presentation table happening to be called DEV_METRICS) is not a hit
    assert s_stat_012(make_ctx("SELECT * FROM PROD.MART.DEV_METRICS"), {}).status is Status.PASS


def test_raw_layer_direct_reference_fails():
    assert s_stat_013(make_ctx("SELECT * FROM RAW.SALESFORCE.ACCOUNT"), {}).status is Status.FAIL
    assert s_stat_013(make_ctx("SELECT * FROM MYDB.STG.ORDERS"), {}).status is Status.FAIL
    assert s_stat_013(make_ctx("SELECT * FROM ANALYTICS.MART.ORDERS"), {}).status is Status.PASS


def test_outer_join_nullified_by_where_warns():
    res = s_stat_014(make_ctx("SELECT a.x FROM a LEFT JOIN b ON a.id=b.id WHERE b.status = 1"), {})
    assert res.status is Status.WARN
    assert "LEFT JOIN" in (res.observed or "")


def test_outer_join_is_fine_when_null_tolerant_or_in_on_clause():
    # anti-join (col IS NULL) and a condition kept in the ON clause are both correct
    anti = "SELECT a.x FROM a LEFT JOIN b ON a.id=b.id WHERE b.id IS NULL"
    on_clause = "SELECT a.x FROM a LEFT JOIN b ON a.id=b.id AND b.status=1"
    assert s_stat_014(make_ctx(anti), {}).status is Status.PASS
    assert s_stat_014(make_ctx(on_clause), {}).status is Status.PASS


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

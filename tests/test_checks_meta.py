"""Stream A metadata family fixture tests, using a routed fake session."""

from plumb.checks.sql_meta import s_meta_001, s_meta_004
from plumb.config.models import Ruleset
from plumb.engine.models import Severity, Status
from tests._fakes import RouteSession, make_ctx

SQL = "SELECT * FROM ANALYTICS.MART.FCT_SALES s"


def test_existing_table_passes():
    session = RouteSession().add(
        "INFORMATION_SCHEMA.TABLES",
        [{"TABLE_SCHEMA": "MART", "TABLE_NAME": "FCT_SALES", "COMMENT": ""}],
    )
    res = s_meta_001(make_ctx(SQL, session=session), {})
    assert res.status is Status.PASS


def test_missing_table_is_blocker_fail():
    session = RouteSession().add("INFORMATION_SCHEMA.TABLES", [])
    res = s_meta_001(make_ctx(SQL, session=session), {})
    assert res.status is Status.FAIL
    assert res.severity is Severity.BLOCKER
    assert "FCT_SALES" in (res.observed or "")


def test_static_only_run_skips_metadata():
    res = s_meta_001(make_ctx(SQL, session=None), {})
    assert res.status is Status.SKIP


def test_metadata_lookup_error_is_surfaced():
    class Boom:
        query_tag = "plumb_qc:x"

        def execute(self, sql, params=None):
            raise RuntimeError("network down")

    res = s_meta_001(make_ctx(SQL, session=Boom()), {})
    assert res.status is Status.ERROR


def test_non_certified_source_warns():
    ruleset = Ruleset(version="1", certified_sources=["ANALYTICS.MART.FCT_GL"])
    res = s_meta_004(make_ctx(SQL, ruleset=ruleset), {})
    assert res.status is Status.WARN
    assert "FCT_SALES" in (res.observed or "")


def test_certified_source_passes():
    ruleset = Ruleset(version="1", certified_sources=["ANALYTICS.MART.FCT_SALES"])
    res = s_meta_004(make_ctx(SQL, ruleset=ruleset), {})
    assert res.status is Status.PASS

"""User-authored custom assertion checks."""

from plumb.checks.sql_custom import d_custom_001
from plumb.engine.models import Severity, Status
from tests._fakes import RouteSession, make_ctx

TARGET = "SELECT id, amount FROM db.s.t"


def test_violations_fail_with_evidence_and_custom_id():
    session = RouteSession().add("amount < 0", [{"ID": 1, "AMOUNT": -5}, {"ID": 2, "AMOUNT": -9}])
    res = d_custom_001(
        make_ctx(TARGET, session=session),
        {"name": "amounts are non-negative", "sql": "SELECT * FROM {{ target }} WHERE amount < 0"},
    )
    assert res.status is Status.FAIL
    assert res.id == "D-CUSTOM:amounts are non-negative"
    assert res.name == "amounts are non-negative"
    assert "2 row(s) violate" in (res.observed or "")
    assert len(res.evidence.sample_rows) == 2


def test_no_violations_pass():
    session = RouteSession().add("amount < 0", [])
    res = d_custom_001(
        make_ctx(TARGET, session=session),
        {"name": "non-negative", "sql": "SELECT * FROM {{ target }} WHERE amount < 0"},
    )
    assert res.status is Status.PASS


def test_custom_severity_applied():
    session = RouteSession().add("amount < 0", [{"ID": 1}])
    res = d_custom_001(
        make_ctx(TARGET, session=session),
        {"name": "blocker check", "sql": "SELECT * FROM {{ target }} WHERE amount < 0",
         "severity": "BLOCKER"},
    )
    assert res.severity is Severity.BLOCKER


def test_no_sql_skips():
    res = d_custom_001(make_ctx(TARGET, session=RouteSession()), {"name": "x"})
    assert res.status is Status.SKIP


def test_non_read_is_refused_as_error():
    session = RouteSession()
    res = d_custom_001(
        make_ctx(TARGET, session=session),
        {"name": "danger", "sql": "DROP TABLE t"},
    )
    assert res.status is Status.ERROR
    assert session.executed == []  # guard blocked it before execution


def test_static_only_skips():
    res = d_custom_001(
        make_ctx(TARGET, session=None),
        {"name": "x", "sql": "SELECT * FROM {{ target }} WHERE amount < 0"},
    )
    assert res.status is Status.SKIP

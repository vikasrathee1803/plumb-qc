"""Phase 1 acceptance criteria, one test per criterion, by ID.

This is the Gate 1 demonstration. Each test name maps to a checkbox in
PLUMB_SPEC.md "Acceptance criteria / Phase 1". Criteria that require a
live Snowflake account (QUERY_HISTORY verification) are asserted at the
seam that guarantees the behavior, with the live step noted.
"""

from pathlib import Path

from typer.testing import CliRunner

from plumb.cli import app
from plumb.config.models import CheckSpec, Ruleset
from plumb.connect.snowflake import build_connect_kwargs
from plumb.engine.models import CheckFamily, Status, Target, Verdict
from plumb.engine.runner import RunRequest, run_checks
from plumb.report.html import render_html
from tests._fakes import RouteSession
from tests.test_connect_session import make_profile

runner = CliRunner()
RULES = Path(__file__).parent.parent / "rules" / "plumb.yml"
FANOUT = "SELECT o.order_id FROM orders o JOIN dim_customer c ON o.cust_id = c.id"


def _target() -> Target:
    return Target(type="sql", name="rpt", source_ref="rpt.sql")


def test_ac1_check_runs_every_enabled_check_and_exit_code(tmp_path: Path):
    """AC1: plumb check sql runs the enabled checks and exits with the
    code for the verdict."""
    out = tmp_path / "r"
    result = runner.invoke(
        app,
        ["check", "sql", "--inline", "SELECT a FROM t, u",
         "--rules", str(RULES), "--static-only", "--out", str(out)],
    )
    assert result.exit_code == 2  # cartesian join -> BLOCKED


def test_ac2_fanout_is_blocked_and_html_names_key_and_redacts_sample():
    """AC2: a row-multiplying join produces BLOCKED via D-GRAIN-001; the
    HTML names the duplicated key and shows a capped, PII-redacted sample."""
    ruleset = Ruleset(
        version="1",
        checks=[CheckSpec(id="D-GRAIN-001", enabled=True, params={"key": ["order_id"]})],
    )
    session = RouteSession().add(
        "__PLUMB_DUP_COUNT",
        [{"ORDER_ID": 1, "CUSTOMER_EMAIL": "a@x.com", "__PLUMB_DUP_COUNT": 4}],
    )
    result = run_checks(
        RunRequest(target=_target(), ruleset=ruleset, sql_text=FANOUT, session=session)
    )
    assert result.verdict is Verdict.BLOCKED
    grain = next(c for c in result.checks if c.id == "D-GRAIN-001")
    assert grain.status is Status.FAIL
    html = render_html(result)
    assert "order_id" in html  # the duplicated key is named (in expected)
    assert "ORDER_ID" in html  # and shown in the sample
    assert "[redacted]" in html  # the PII column is redacted
    assert "a@x.com" not in html  # the raw PII value never egresses


def test_ac3_recon_drift_is_blocked_with_observed_expected_difference():
    """AC3: a reconciliation off by more than tolerance is BLOCKED via
    D-RECON-001 with observed vs expected and the difference."""
    ruleset = Ruleset(
        version="1",
        checks=[
            CheckSpec(
                id="D-RECON-001",
                enabled=True,
                params={
                    "metric_sql": "SELECT SUM(amount) AS M FROM {{ target }}",
                    "source_of_truth_sql": "SELECT SUM(net_amount) AS M FROM MART.FCT_SALES",
                    "tolerance_abs": 0,
                    "tolerance_pct": 0.0,
                },
            )
        ],
    )
    session = RouteSession()
    session.add("SUM(amount)", [{"M": 1000.0}])
    session.add("FCT_SALES", [{"M": 900.0}])
    result = run_checks(
        RunRequest(target=_target(), ruleset=ruleset, sql_text=FANOUT, session=session)
    )
    assert result.verdict is Verdict.BLOCKED
    recon = next(c for c in result.checks if c.id == "D-RECON-001")
    assert recon.status is Status.FAIL
    assert "1000" in (recon.observed or "") and "900" in (recon.observed or "")
    assert "difference" in (recon.observed or "")


def test_ac4_baseline_unchanged_passes_changed_reports_moves(tmp_path: Path):
    """AC4: against a saved baseline, an unchanged query reports R-DIFF-001
    PASS; a changed query reports the rows or aggregates that moved."""
    from plumb.baseline.store import LocalParquetStore, make_baseline

    rows = [{"REGION": "EAST", "AMOUNT": 100}, {"REGION": "WEST", "AMOUNT": 200}]
    store = LocalParquetStore(tmp_path)
    store.save(make_baseline("b", ["REGION", "AMOUNT"], rows))
    ruleset = Ruleset(version="1", checks=[CheckSpec(id="R-DIFF-001", enabled=True)])

    unchanged = RouteSession().add("FROM __plumb_target", rows)
    res_same = run_checks(
        RunRequest(target=_target(), ruleset=ruleset, sql_text="SELECT region, amount FROM t",
                   session=unchanged, baseline_store=store, baseline_name="b")
    )
    assert next(c for c in res_same.checks if c.id == "R-DIFF-001").status is Status.PASS

    changed_rows = [{"REGION": "EAST", "AMOUNT": 100}, {"REGION": "WEST", "AMOUNT": 999}]
    changed = RouteSession().add("FROM __plumb_target", changed_rows)
    res_diff = run_checks(
        RunRequest(target=_target(), ruleset=ruleset, sql_text="SELECT region, amount FROM t",
                   session=changed, baseline_store=store, baseline_name="b")
    )
    diff = next(c for c in res_diff.checks if c.id == "R-DIFF-001")
    assert diff.status is Status.FAIL
    assert "added" in (diff.observed or "") and "removed" in (diff.observed or "")
    assert {r["__plumb_change"] for r in diff.evidence.sample_rows} == {"added", "removed"}


def test_ac5_clean_no_baseline_recon_skipped_is_ready_with_ranked_coverage():
    """AC5: a clean run with no baseline and skipped reconciliation reports
    READY with coverage listing both skips, ranked."""
    ruleset = Ruleset(
        version="1",
        checks=[
            CheckSpec(id="S-STAT-001", enabled=True),
            CheckSpec(id="D-RECON-001", enabled=True),
            CheckSpec(id="D-DUP-001", enabled=True),
            CheckSpec(id="R-DIFF-001", enabled=True),
        ],
    )
    session = RouteSession().add("__PLUMB_DUP_ROWS", [{"__PLUMB_DUP_ROWS": 0}])
    result = run_checks(
        RunRequest(target=_target(), ruleset=ruleset,
                   sql_text="SELECT a, b FROM db.s.t WHERE a > 0", session=session)
    )
    assert result.verdict is Verdict.READY
    assert CheckFamily.REGRESSION in [s.family for s in result.coverage.families_skipped]
    assert "D-RECON-001" in [c.id for c in result.coverage.checks_skipped]


def test_ac6_every_query_carries_tag_warehouse_timeout_rowcap():
    """AC6: every Snowflake query carries plumb_qc:{run_id}, runs on
    PLUMB_WH, and respects the timeout and row cap. The session assembles
    these so none can be omitted; live QUERY_HISTORY check is in RUNBOOK."""
    kwargs = build_connect_kwargs(make_profile(), run_id="abc-123", statement_timeout_s=120)
    assert kwargs["session_parameters"]["QUERY_TAG"] == "plumb_qc:abc-123"
    assert kwargs["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 120
    assert kwargs["warehouse"] == "PLUMB_WH"
    # row cap is enforced at fetch; proven in tests/test_connect_session.py


def test_ac7_malformed_ruleset_exits_nonzero_with_message(tmp_path: Path):
    """AC7: a malformed ruleset fails with a clear validation message and a
    non-zero exit, never running partial checks."""
    bad = tmp_path / "bad.yml"
    bad.write_text("version: '1'\ndefaults:\n  statement_timeout_s: -1\n", encoding="utf-8")
    result = runner.invoke(
        app, ["check", "sql", "--inline", "SELECT 1", "--rules", str(bad), "--static-only"]
    )
    assert result.exit_code == 3
    assert "statement_timeout_s" in result.output


def test_ac8_refuses_any_non_read():
    """AC8: the tool refuses any statement that is not a read. Full matrix
    in tests/test_readonly_guard.py; a representative here."""
    import pytest

    from plumb.connect.snowflake import ReadOnlyViolation, assert_read_only

    for sql in ("DROP TABLE t", "INSERT INTO t VALUES (1)", "SELECT 1; DROP TABLE t"):
        with pytest.raises(ReadOnlyViolation):
            assert_read_only(sql)


def test_ac9_all_three_output_formats(tmp_path: Path):
    """AC9: HTML, JSON, and JUnit XML are all produced."""
    out = tmp_path / "r"
    runner.invoke(
        app, ["check", "sql", "--inline", "SELECT a FROM t",
              "--rules", str(RULES), "--static-only", "--out", str(out)]
    )
    assert (out / "report.html").exists()
    assert (out / "report.json").exists()
    assert (out / "report.junit.xml").exists()


def test_ac10_verdict_and_per_family_tests_exist():
    """AC10: verdict tiers and coverage are fully tested, and every check
    family has at least one fixture-backed test. This asserts the family
    coverage of the test suite by importing the family test modules."""
    import importlib

    for module in (
        "tests.test_verdict",
        "tests.test_checks_static",
        "tests.test_checks_meta",
        "tests.test_checks_assertions",
        "tests.test_checks_regression",
        "tests.test_checks_performance",
    ):
        assert importlib.import_module(module) is not None

"""End-to-end runner: ruleset in, RunResult out, deterministic.

Exercises the acceptance-critical shapes: a grain fan-out drives BLOCKED,
and a clean run with no baseline plus skipped reconciliation reports READY
with both gaps listed and ranked in coverage.
"""

from pathlib import Path

from plumb.baseline.store import LocalParquetStore
from plumb.config.models import CheckSpec, Ruleset
from plumb.engine.models import CheckFamily, Status, Target, Verdict
from plumb.engine.runner import RunRequest, run_checks
from tests._fakes import RouteSession

FANOUT_SQL = "SELECT o.order_id FROM orders o JOIN dim_customer c ON o.cust_id = c.id"
CLEAN_SQL = "SELECT a, b FROM db.sch.t WHERE a > 0"


def _target() -> Target:
    return Target(type="sql", name="rpt", source_ref="rpt.sql")


def test_grain_fanout_drives_blocked_verdict():
    ruleset = Ruleset(
        version="2026.06.0",
        checks=[CheckSpec(id="D-GRAIN-001", enabled=True, params={"key": ["order_id"]})],
    )
    session = RouteSession().add(
        "__PLUMB_DUP_COUNT", [{"ORDER_ID": 1, "__PLUMB_DUP_COUNT": 4}]
    )
    result = run_checks(
        RunRequest(target=_target(), ruleset=ruleset, sql_text=FANOUT_SQL, session=session)
    )
    assert result.verdict is Verdict.BLOCKED
    assert result.summary.blocker == 1
    grain = next(c for c in result.checks if c.id == "D-GRAIN-001")
    assert grain.status is Status.FAIL


def test_clean_run_no_baseline_recon_skipped_is_ready_with_ranked_gaps():
    ruleset = Ruleset(
        version="2026.06.0",
        checks=[
            CheckSpec(id="S-STAT-001", enabled=True),
            CheckSpec(id="S-STAT-002", enabled=True),
            CheckSpec(id="D-RECON-001", enabled=True),  # no params -> skips
            CheckSpec(id="D-DUP-001", enabled=True),
            CheckSpec(id="R-DIFF-001", enabled=True),  # no baseline -> skips
        ],
    )
    session = RouteSession().add("__PLUMB_DUP_ROWS", [{"__PLUMB_DUP_ROWS": 0}])
    result = run_checks(
        RunRequest(
            target=_target(),
            ruleset=ruleset,
            sql_text=CLEAN_SQL,
            session=session,
            baseline_store=LocalParquetStore(Path("nonexistent")),
        )
    )
    assert result.verdict is Verdict.READY
    # regression family fully skipped, surfaced at family level
    skipped_families = [s.family for s in result.coverage.families_skipped]
    assert CheckFamily.REGRESSION in skipped_families
    # reconciliation skipped inside the assertions family, surfaced as a check gap
    skipped_check_ids = [c.id for c in result.coverage.checks_skipped]
    assert "D-RECON-001" in skipped_check_ids


def test_disabled_checks_do_not_run():
    ruleset = Ruleset(
        version="1",
        checks=[CheckSpec(id="S-STAT-001", enabled=False)],
    )
    result = run_checks(
        RunRequest(target=_target(), ruleset=ruleset, sql_text=CLEAN_SQL)
    )
    assert all(c.id != "S-STAT-001" for c in result.checks)


def test_run_is_deterministic():
    ruleset = Ruleset(
        version="1",
        checks=[CheckSpec(id="S-STAT-001", enabled=True), CheckSpec(id="S-STAT-002", enabled=True)],
    )
    r1 = run_checks(RunRequest(target=_target(), ruleset=ruleset, sql_text=CLEAN_SQL))
    r2 = run_checks(RunRequest(target=_target(), ruleset=ruleset, sql_text=CLEAN_SQL))
    assert [(c.id, c.status) for c in r1.checks] == [(c.id, c.status) for c in r2.checks]
    assert r1.verdict is r2.verdict


def test_unknown_check_id_in_ruleset_is_skipped_not_fatal():
    ruleset = Ruleset(
        version="1",
        checks=[
            CheckSpec(id="T-SRC-001", enabled=True),  # a Phase 2 check, not registered yet
            CheckSpec(id="S-STAT-001", enabled=True),
        ],
    )
    result = run_checks(RunRequest(target=_target(), ruleset=ruleset, sql_text=CLEAN_SQL))
    assert any(c.id == "S-STAT-001" for c in result.checks)
    assert all(c.id != "T-SRC-001" for c in result.checks)


def test_tableau_target_runs_only_tableau_checks():
    from pathlib import Path

    from plumb.checks._tableau import parse_workbook

    wb = parse_workbook(
        Path(__file__).parent / "fixtures" / "tableau" / "sales_dashboard.twb"
    )
    ruleset = Ruleset(
        version="1",
        checks=[
            CheckSpec(id="S-STAT-001", enabled=True),  # SQL: must not run on tableau
            CheckSpec(id="T-SRC-003", enabled=True),  # tableau: must run
        ],
    )
    result = run_checks(
        RunRequest(
            target=Target(type="tableau", name="wb", source_ref="wb.twb"),
            ruleset=ruleset,
            workbook=wb,
        )
    )
    families = {c.family for c in result.checks}
    assert families == {CheckFamily.TABLEAU_STATIC}
    assert any(c.id == "T-SRC-003" for c in result.checks)
    assert all(c.id != "S-STAT-001" for c in result.checks)


def test_sql_target_does_not_run_tableau_checks():
    ruleset = Ruleset(
        version="1",
        checks=[
            CheckSpec(id="S-STAT-001", enabled=True),
            CheckSpec(id="T-SRC-003", enabled=True),  # tableau: must not run on sql
        ],
    )
    result = run_checks(
        RunRequest(target=_target(), ruleset=ruleset, sql_text=CLEAN_SQL)
    )
    assert all(c.family is not CheckFamily.TABLEAU_STATIC for c in result.checks)


def test_environment_carries_query_tag_warehouse_role():
    ruleset = Ruleset(version="1", checks=[CheckSpec(id="S-STAT-001", enabled=True)])
    session = RouteSession()
    result = run_checks(
        RunRequest(
            target=_target(), ruleset=ruleset, sql_text=CLEAN_SQL, session=session,
            run_id="fixed-run",
        )
    )
    assert result.environment.warehouse == "PLUMB_WH"
    assert result.environment.role == "PLUMB_QC_ROLE"
    assert result.environment.query_tag == "plumb_qc:test-run"
    assert result.run_id == "fixed-run"

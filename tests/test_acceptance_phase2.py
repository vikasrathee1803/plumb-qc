"""Phase 2 acceptance criteria, one test per criterion, by ID.

Maps to PLUMB_SPEC.md "Acceptance criteria / Phase 2".
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from plumb.ai.client import AIClient
from plumb.ai.explain import attach_explanations
from plumb.baseline.store import SharedFileStore, make_baseline
from plumb.checks._tableau import parse_workbook
from plumb.config.models import CheckSpec, Ruleset
from plumb.engine.models import Status, Target, Verdict
from plumb.engine.runner import RunRequest, run_checks
from tests._fakes import RouteSession
from web.api.app import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "tableau" / "sales_dashboard.twb"
client = TestClient(create_app())


def test_p2ac1_tableau_catalog_with_lod_and_custom_sql():
    """P2-AC1: check tableau parses the workbook and reports the catalog,
    including a FIXED LOD inventory and any custom SQL."""
    wb = parse_workbook(FIXTURE)
    ruleset = Ruleset(
        version="1",
        checks=[CheckSpec(id="T-LOD-001", enabled=True), CheckSpec(id="T-SRC-001", enabled=True)],
    )
    result = run_checks(
        RunRequest(
            target=Target(type="tableau", name="wb"), ruleset=ruleset, workbook=wb
        )
    )
    lod = next(c for c in result.checks if c.id == "T-LOD-001")
    src = next(c for c in result.checks if c.id == "T-SRC-001")
    assert lod.status is Status.WARN
    assert any("FIXED" in (r["formula"] or "") for r in lod.evidence.sample_rows)
    assert src.status is Status.WARN and "Raw Orders Extract" in (src.observed or "")


def test_p2ac2_web_ui_renders_same_verdict_as_cli():
    """P2-AC2: the web UI runs from one command and renders the same verdict
    and report as the CLI (same engine, same RunResult contract)."""
    sql = "SELECT a FROM t, u"
    web = client.post("/api/check/sql", json={"sql": sql, "static_only": True}).json()

    # The CLI path uses the same runner; reproduce it directly.
    from plumb.config.loader import load_ruleset

    ruleset = load_ruleset(Path("rules/plumb.yml"), enforce_pin=False)
    cli_result = run_checks(
        RunRequest(target=Target(type="sql", name="web_sql"), ruleset=ruleset, sql_text=sql)
    )
    assert web["verdict"] == cli_result.verdict.value == "BLOCKED"
    assert [c["id"] for c in web["checks"]] == [c.id for c in cli_result.checks]

    report = client.get(f"/api/report/{web['run_id']}.html")
    assert report.status_code == 200 and "BLOCKED" in report.text


def test_p2ac3_explain_never_alters_a_status():
    """P2-AC3: --explain attaches explanations and demonstrably never alters
    a status (verdict equality with and without the flag)."""
    ruleset = Ruleset(
        version="1",
        checks=[CheckSpec(id="D-GRAIN-001", enabled=True, params={"key": ["id"]})],
    )

    def run():
        session = RouteSession().add("__PLUMB_DUP_COUNT", [{"ID": 1, "__PLUMB_DUP_COUNT": 3}])
        return run_checks(
            RunRequest(
                target=Target(type="sql", name="t"),
                ruleset=ruleset,
                sql_text="SELECT id FROM a JOIN b ON a.k = b.k",
                session=session,
            )
        )

    baseline = run()
    explained = run()
    fake = AIClient(
        complete=lambda s, u, m: json.dumps(
            {"root_cause": "fan-out", "business_impact": "overstated", "confidence": "high"}
        )
    )
    attach_explanations(explained, fake, "sql")

    assert baseline.verdict is explained.verdict is Verdict.BLOCKED
    assert [c.status for c in baseline.checks] == [c.status for c in explained.checks]
    assert explained.checks[0].ai_explanation is not None


def test_p2ac4_shared_baseline_reproduces_diff_for_a_teammate(tmp_path: Path):
    """P2-AC4: shared baselines write to and read from the configured store,
    and a teammate's machine reproduces the same diff."""
    shared = tmp_path / "team_store"
    rows = [{"REGION": "EAST", "AMOUNT": 100}, {"REGION": "WEST", "AMOUNT": 200}]
    SharedFileStore(shared).save(make_baseline("sales", ["REGION", "AMOUNT"], rows))

    ruleset = Ruleset(version="1", checks=[CheckSpec(id="R-DIFF-001", enabled=True)])
    changed = [{"REGION": "EAST", "AMOUNT": 100}, {"REGION": "WEST", "AMOUNT": 999}]
    teammate_store = SharedFileStore(shared)  # different machine, same path
    result = run_checks(
        RunRequest(
            target=Target(type="sql", name="t"),
            ruleset=ruleset,
            sql_text="SELECT region, amount FROM rpt",
            session=RouteSession().add("FROM __plumb_target", changed),
            baseline_store=teammate_store,
            baseline_name="sales",
        )
    )
    diff = next(c for c in result.checks if c.id == "R-DIFF-001")
    assert diff.status is Status.FAIL
    assert "1 rows added, 1 rows removed" in (diff.observed or "")

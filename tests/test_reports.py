"""Report writers: all three consume the same RunResult and produce valid
output. No em dashes may appear in any generated report."""

import json
from pathlib import Path
from xml.etree import ElementTree as ET

from plumb.engine.models import (
    CheckFamily,
    CheckResult,
    Coverage,
    Environment,
    Evidence,
    RunResult,
    Severity,
    SkippedCheck,
    SkippedFamily,
    Status,
    Summary,
    Target,
    Verdict,
    utc_now,
)
from plumb.report.html import render_html
from plumb.report.json_out import render_json
from plumb.report.junit import render_junit


def _result() -> RunResult:
    checks = [
        CheckResult(
            id="D-GRAIN-001",
            name="Grain uniqueness on declared key",
            family=CheckFamily.ASSERTIONS,
            severity=Severity.BLOCKER,
            status=Status.FAIL,
            observed="12 duplicate key groups, max duplication 4x",
            expected="0 duplicates on [order_id]",
            evidence=Evidence(
                query="SELECT 1",
                sample_rows=[{"ORDER_ID": 1, "CUSTOMER_EMAIL": "[redacted]"}],
            ),
            remediation="Aggregate to grain or fix the join key.",
        ),
        CheckResult(
            id="S-STAT-001",
            name="SELECT * in a production query",
            family=CheckFamily.STATIC,
            severity=Severity.HIGH,
            status=Status.PASS,
        ),
    ]
    return RunResult(
        run_id="abc-123",
        timestamp=utc_now(),
        target=Target(type="sql", name="rpt_daily_sales", source_ref="q.sql"),
        ruleset_version="2026.06.0",
        profile="finance",
        verdict=Verdict.BLOCKED,
        coverage=Coverage(
            families_run=[CheckFamily.ASSERTIONS, CheckFamily.STATIC],
            families_skipped=[
                SkippedFamily(family=CheckFamily.REGRESSION, reason="no baseline found")
            ],
            checks_skipped=[
                SkippedCheck(
                    id="D-RECON-001",
                    name="Aggregates tie to a source of truth",
                    family=CheckFamily.ASSERTIONS,
                    reason="needs metric_sql and source_of_truth_sql in params",
                )
            ],
        ),
        summary=Summary(blocker=1, passed=1, total=2),
        checks=checks,
        environment=Environment(warehouse="PLUMB_WH", role="PLUMB_QC_ROLE"),
    )


def test_json_round_trips():
    out = render_json(_result())
    parsed = json.loads(out)
    assert parsed["verdict"] == "BLOCKED"
    assert parsed["checks"][0]["id"] == "D-GRAIN-001"
    assert parsed["coverage"]["families_skipped"][0]["family"] == "regression"


def test_junit_is_well_formed_and_marks_failure():
    xml = render_junit(_result())
    root = ET.fromstring(xml)
    assert root.tag == "testsuite"
    assert root.attrib["failures"] == "1"
    assert root.attrib["tests"] == "2"
    assert root.attrib["errors"] == "0"
    failure = root.find(".//failure")
    assert failure is not None
    assert failure.attrib["type"] == "BLOCKER"


def test_html_is_self_contained_and_names_the_evidence():
    html = render_html(_result())
    assert "<link" not in html and "<script src" not in html
    assert "BLOCKED" in html
    assert "D-GRAIN-001" in html
    assert "[redacted]" in html
    assert "no baseline found" in html


def test_no_em_dash_in_any_report():
    result = _result()
    for rendered in (render_json(result), render_junit(result), render_html(result)):
        assert "—" not in rendered
        assert "–" not in rendered


def test_html_escapes_evidence_values():
    result = _result()
    result.checks[0].evidence.sample_rows = [{"NOTE": "<script>alert(1)</script>"}]
    html = render_html(result)
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


def test_writers_persist_to_disk(tmp_path: Path):
    from plumb.report.html import write_html
    from plumb.report.json_out import write_json
    from plumb.report.junit import write_junit

    result = _result()
    h = write_html(result, tmp_path / "report.html")
    j = write_json(result, tmp_path / "report.json")
    x = write_junit(result, tmp_path / "report.junit.xml")
    assert h.exists() and j.exists() and x.exists()

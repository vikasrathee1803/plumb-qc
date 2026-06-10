"""Tests for the estate report writers (PARITY-PLAN-V2 S7.3).

The HTML roll-up must name every workbook with its per-phase and worst
verdicts (and escape hostile values), and the JUnit file must parse as
one testcase per workbook with the error/failure/pass mapping CI gates
rely on: error -> <error>, BLOCKED/REVIEW -> <failure>,
READY_WITH_NOTES -> pass with a note, READY -> plain pass.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from plumb.engine.models import Coverage, RunResult, Summary, Target, Verdict, utc_now
from plumb.parity.contracts import EstateResult, WorkbookParity
from plumb.report.estate import write_estate_html, write_estate_junit


def fake_run(verdict: Verdict) -> RunResult:
    return RunResult(
        run_id="run-1",
        timestamp=utc_now(),
        target=Target(type="parity", name="wb"),
        ruleset_version="test",
        verdict=verdict,
        coverage=Coverage(),
        summary=Summary(),
    )


def sample_estate() -> EstateResult:
    entries = [
        WorkbookParity(
            workbook_path="wave/kpi.twb",
            map_path="maps/kpi.yml",
            snapshot_result=fake_run(Verdict.READY),
            check_result=fake_run(Verdict.READY),
        ),
        WorkbookParity(
            workbook_path="wave/sales.twb",
            snapshot_result=fake_run(Verdict.READY),
            check_result=fake_run(Verdict.READY_WITH_NOTES),
        ),
        WorkbookParity(
            workbook_path="wave/inventory.twb",
            check_result=fake_run(Verdict.REVIEW),
        ),
        WorkbookParity(
            workbook_path="wave/blocked.twb",
            check_result=fake_run(Verdict.BLOCKED),
        ),
        WorkbookParity(
            workbook_path="wave/broken.twb",
            error="could not parse workbook XML",
        ),
    ]
    estate = EstateResult(
        phase="run",
        entries=entries,
        manifest_ref="wave.yml",
        created_at="2026-06-10T00:00:00+00:00",
    )
    estate.rollup = estate.compute_rollup()
    return estate


class TestEstateHtml:
    def test_contains_every_workbook_and_the_rollup(self, tmp_path: Path) -> None:
        estate = sample_estate()
        out = tmp_path / "estate.html"
        write_estate_html(estate, out)
        html = out.read_text(encoding="utf-8")
        for entry in estate.entries:
            assert entry.workbook_path in html
        assert "v-BLOCKED" in html  # rollup styling: the wave is blocked
        assert "run" in html and "wave.yml" in html
        assert "2026-06-10T00:00:00+00:00" in html
        assert "maps/kpi.yml" in html
        assert "could not parse workbook XML" in html
        # Per-entry verdict pills.
        assert "READY_WITH_NOTES" in html
        assert "REVIEW" in html
        # Header counts: 5 workbooks, one in each bucket.
        assert "5</b> workbooks" in html
        assert "1 blocked" in html and "1 errored" in html

    def test_values_are_escaped(self, tmp_path: Path) -> None:
        estate = EstateResult(
            phase="check",
            entries=[
                WorkbookParity(
                    workbook_path="<script>alert(1)</script>.twb",
                    error="<b>boom</b>",
                )
            ],
        )
        estate.rollup = estate.compute_rollup()
        out = tmp_path / "estate.html"
        write_estate_html(estate, out)
        html = out.read_text(encoding="utf-8")
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
        assert "<b>boom</b>" not in html

    def test_writes_parent_directories(self, tmp_path: Path) -> None:
        out = tmp_path / "deep" / "nested" / "estate.html"
        write_estate_html(sample_estate(), out)
        assert out.is_file()


class TestEstateJunit:
    def _suite(self, tmp_path: Path, estate: EstateResult) -> ET.Element:
        out = tmp_path / "estate.xml"
        write_estate_junit(estate, out)
        return ET.fromstring(out.read_text(encoding="utf-8"))

    def test_suite_structure_and_counts(self, tmp_path: Path) -> None:
        estate = sample_estate()
        suite = self._suite(tmp_path, estate)
        assert suite.tag == "testsuite"
        assert suite.get("name") == "plumb-parity-estate"
        assert suite.get("tests") == "5"
        assert suite.get("failures") == "2"  # REVIEW + BLOCKED
        assert suite.get("errors") == "1"  # the unparseable workbook
        cases = suite.findall("testcase")
        assert len(cases) == len(estate.entries)
        assert [case.get("name") for case in cases] == [
            entry.workbook_path for entry in estate.entries
        ]
        properties = {
            prop.get("name"): prop.get("value")
            for prop in suite.findall("properties/property")
        }
        assert properties["phase"] == "run"
        assert properties["rollup"] == "BLOCKED"
        assert properties["manifest"] == "wave.yml"

    def test_case_mapping_per_verdict(self, tmp_path: Path) -> None:
        suite = self._suite(tmp_path, sample_estate())
        cases = {case.get("name"): case for case in suite.findall("testcase")}

        broken = cases["wave/broken.twb"]
        (error,) = broken.findall("error")
        assert error.get("message") == "could not parse workbook XML"
        assert broken.findall("failure") == []

        blocked = cases["wave/blocked.twb"]
        (failure,) = blocked.findall("failure")
        assert failure.get("message") == "verdict: BLOCKED"

        review = cases["wave/inventory.twb"]
        (failure,) = review.findall("failure")
        assert failure.get("message") == "verdict: REVIEW"

        notes = cases["wave/sales.twb"]
        assert notes.findall("failure") == [] and notes.findall("error") == []
        (out,) = notes.findall("system-out")
        assert out.text is not None and "READY_WITH_NOTES" in out.text

        ready = cases["wave/kpi.twb"]
        assert ready.findall("failure") == []
        assert ready.findall("error") == []
        assert ready.findall("skipped") == []
        assert ready.findall("system-out") == []

    def test_resultless_entry_without_error_fails_the_case(
        self, tmp_path: Path
    ) -> None:
        estate = EstateResult(
            phase="check",
            entries=[WorkbookParity(workbook_path="wave/ghost.twb")],
        )
        estate.rollup = estate.compute_rollup()
        suite = self._suite(tmp_path, estate)
        assert suite.get("failures") == "1"
        (case,) = suite.findall("testcase")
        (failure,) = case.findall("failure")
        assert "none" in (failure.get("message") or "")

    def test_writes_parent_directories(self, tmp_path: Path) -> None:
        out = tmp_path / "ci" / "junit" / "estate.xml"
        write_estate_junit(sample_estate(), out)
        assert out.is_file()


class TestQcWaveRegressions:
    @staticmethod
    def _estate(entries: list[WorkbookParity]) -> EstateResult:
        estate = EstateResult(phase="run", entries=entries)
        estate.rollup = estate.compute_rollup()
        return estate

    def test_errored_entry_never_shows_a_phase_verdict_as_worst(self) -> None:
        """QC F4: snapshot READY + check-sweep error must not render a green
        READY pill in the Worst column next to red error text."""
        from plumb.report.estate import render_estate_html

        entry = WorkbookParity(
            workbook_path="wb.twbx",
            snapshot_result=fake_run(Verdict.READY),
            error="target session lost",
        )
        html = render_estate_html(self._estate([entry]))
        # The Worst pill is ERROR; READY appears only in the Snapshot column.
        assert 'p-ERROR">ERROR</span>' in html
        assert 'p-READY">READY</span>' in html  # snapshot column, truthful
        assert html.count('p-READY">READY</span>') == 1

    def test_junit_with_control_characters_still_parses(self) -> None:
        """QC F5: estate error strings are raw exception text from hostile
        inputs; a \\x0b in one must not corrupt estate.junit.xml."""
        from plumb.report.estate import render_estate_junit

        entry = WorkbookParity(
            workbook_path="wb\x0b.twbx", error="parse failed: \x00\x0b\x1b junk"
        )
        xml = render_estate_junit(self._estate([entry]))
        parsed = ET.fromstring(xml)  # must not raise "not well-formed"
        error = parsed.find(".//error")
        assert error is not None
        assert "parse failed" in (error.get("message") or "")
        assert "\x0b" not in xml and "\x00" not in xml

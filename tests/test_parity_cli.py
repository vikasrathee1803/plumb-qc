"""End-to-end CLI tests for `plumb parity` (PARITY-PLAN S5.1).

Through typer's CliRunner with the session and baseline store monkey-
patched: the snapshot -> check loop on the custom-SQL fixture, exit codes
per the CI contract (0 passing, 2 BLOCKED on drift, 3 tool error), report
files written, and clean errors (no tracebacks) for bad inputs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import plumb.cli as cli
from plumb.baseline.store import LocalParquetStore
from tests._fakes import RouteSession
from tests._parity_fixtures import TWB_CUSTOM_SQL, TWB_MALFORMED, write_twb

runner = CliRunner()

RULES = str(Path(__file__).resolve().parents[1] / "rules" / "plumb.yml")


@pytest.fixture()
def patched(monkeypatch, tmp_path):
    """One store and one routable session shared across CLI invocations."""
    store = LocalParquetStore(tmp_path / "store")
    session = RouteSession(routes=[("SELECT COUNT(*)", [{"ROW_COUNT": 42}])])
    monkeypatch.setattr(cli, "_baseline_store", lambda: store)
    monkeypatch.setattr(cli, "_open_session", lambda ruleset, run_id, connection_path=None: session)
    monkeypatch.setattr(cli, "LATEST_DIR", tmp_path / "reports")
    return store, session, tmp_path


def _snapshot(wb: Path, out: Path | None = None) -> object:
    args = ["parity", "snapshot", "--workbook", str(wb), "--rules", RULES]
    if out:
        args += ["--out", str(out)]
    return runner.invoke(cli.app, args)


class TestSnapshotCommand:
    def test_snapshot_passes_and_writes_reports(self, patched):
        store, _session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        out = tmp_path / "out"
        result = _snapshot(wb, out)
        assert result.exit_code == 0, result.output
        assert len(store.list_names()) == 1
        for name in ("report.html", "report.json", "report.junit.xml"):
            assert (out / name).exists()

    def test_missing_workbook_is_clean_exit_3(self, patched):
        _store, _session, tmp_path = patched
        result = runner.invoke(
            cli.app,
            ["parity", "snapshot", "--workbook", str(tmp_path / "nope.twb"), "--rules", RULES],
        )
        assert result.exit_code == 3
        assert "Traceback" not in result.output

    def test_malformed_workbook_is_clean_exit_3(self, patched):
        _store, _session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_MALFORMED, "bad.twb")
        result = _snapshot(wb)
        assert result.exit_code == 3
        assert "Traceback" not in result.output

    def test_malformed_workbook_never_opens_a_session(self, patched, monkeypatch):
        """QC F16: inputs are pre-flighted before any session opens; a bad
        workbook must never cost a connection."""
        _store, _session, tmp_path = patched
        calls: list[object] = []

        def counting_open(*args, **kwargs):
            calls.append(args)
            raise AssertionError("session must not be opened for a malformed workbook")

        monkeypatch.setattr(cli, "_open_session", counting_open)
        wb = write_twb(tmp_path, TWB_MALFORMED, "bad.twb")
        result = _snapshot(wb)
        assert result.exit_code == 3
        assert calls == []

    def test_bad_map_content_never_opens_a_session(self, patched, monkeypatch):
        _store, _session, tmp_path = patched
        calls: list[object] = []

        def counting_open(*args, **kwargs):
            calls.append(args)
            raise AssertionError("session must not be opened for a bad map")

        monkeypatch.setattr(cli, "_open_session", counting_open)
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        bad_map = tmp_path / "map.yml"
        bad_map.write_text("version: 1\nnonsense_key: true\n", encoding="utf-8")
        result = runner.invoke(
            cli.app,
            [
                "parity", "snapshot", "--workbook", str(wb),
                "--map", str(bad_map), "--rules", RULES,
            ],
        )
        assert result.exit_code == 3
        assert calls == []
        assert "Traceback" not in result.output

    def test_missing_map_file_is_clean_exit_3(self, patched):
        _store, _session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        result = runner.invoke(
            cli.app,
            [
                "parity", "snapshot", "--workbook", str(wb),
                "--map", str(tmp_path / "missing.yml"), "--rules", RULES,
            ],
        )
        assert result.exit_code == 3
        assert "map file not found" in result.output


class TestCheckCommand:
    def test_matching_counts_exit_0(self, patched):
        _store, _session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        assert _snapshot(wb).exit_code == 0
        result = runner.invoke(
            cli.app, ["parity", "check", "--workbook", str(wb), "--rules", RULES]
        )
        assert result.exit_code == 0, result.output

    def test_drift_exits_blocked(self, patched):
        _store, session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        assert _snapshot(wb).exit_code == 0
        session.routes[0] = ("SELECT COUNT(*)", [{"ROW_COUNT": 99}])
        result = runner.invoke(
            cli.app, ["parity", "check", "--workbook", str(wb), "--rules", RULES]
        )
        assert result.exit_code == 2
        assert "BLOCKED" in result.output

    def test_corrupt_snapshot_is_loud_and_traceback_free(self, patched):
        """QC F8: a truncated snapshot parquet must surface as a named
        M-SNAP-001 failure (exit 2, BLOCKED), never a traceback."""
        _store, _session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        assert _snapshot(wb).exit_code == 0
        parquet_files = list((tmp_path / "store").glob("*.parquet"))
        assert len(parquet_files) == 1
        parquet_files[0].write_bytes(b"truncated")
        result = runner.invoke(
            cli.app, ["parity", "check", "--workbook", str(wb), "--rules", RULES]
        )
        assert result.exit_code == 2
        assert "Traceback" not in result.output
        assert "BLOCKED" in result.output

    def test_check_without_snapshot_exits_blocked(self, patched):
        _store, _session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        result = runner.invoke(
            cli.app, ["parity", "check", "--workbook", str(wb), "--rules", RULES]
        )
        assert result.exit_code == 2

    def test_static_only_never_touches_session(self, patched, monkeypatch):
        _store, _session, tmp_path = patched

        def boom(*args, **kwargs):
            raise AssertionError("static-only must not open a session")

        monkeypatch.setattr(cli, "_open_session", boom)
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        result = runner.invoke(
            cli.app,
            ["parity", "snapshot", "--workbook", str(wb), "--rules", RULES, "--static-only"],
        )
        assert result.exit_code == 0, result.output


# --- v2: both-live run alias + post-swap check (PARITY-PLAN-V2 D16/D14) ---

from tests._parity_fixtures import TWB_TWO_TABLES  # noqa: E402

TWB_TWO_TABLES_SWAPPED = (
    TWB_TWO_TABLES.replace("dbname='LEGACY_DB' schema='SALES'", "dbname='GALAXY' schema='PRES'")
    .replace("dbname='LEGACY_DB' schema='CRM'", "dbname='GALAXY' schema='PRES'")
    .replace("[SALES].[ORDERS]", "[PRES].[ORDERS]")
    .replace("[CRM].[CUSTOMERS]", "[PRES].[CUSTOMERS]")
)

MAP_SWAP = """\
version: 1
objects:
  - old: LEGACY_DB.SALES.ORDERS
    new: GALAXY.PRES.ORDERS
  - old: LEGACY_DB.CRM.CUSTOMERS
    new: GALAXY.PRES.CUSTOMERS
"""

ORDERS_METRICS = [
    {"ROW_COUNT": 42, "NULL_0": 2, "SUM_0": 500.0, "MIN_0": 1.0, "MAX_0": 9.0}
]
CUSTOMERS_METRICS = [{"ROW_COUNT": 7, "NULL_0": 0}]


@pytest.fixture()
def patched_tables(monkeypatch, tmp_path):
    """A session routed for the TWO_TABLES fixture on BOTH sides: legacy
    FQNs answer the snapshot phase, galaxy FQNs answer the check phase."""
    store = LocalParquetStore(tmp_path / "store")
    session = RouteSession(
        routes=[
            ("TABLE_NAME = 'ORDERS'", [{"COLUMN_NAME": "SALES", "DATA_TYPE": "NUMBER"}]),
            (
                "TABLE_NAME = 'CUSTOMERS'",
                [{"COLUMN_NAME": "CUSTOMER_ID", "DATA_TYPE": "TEXT"}],
            ),
            ('"LEGACY_DB"."SALES"."ORDERS"', list(ORDERS_METRICS)),
            ('"LEGACY_DB"."CRM"."CUSTOMERS"', list(CUSTOMERS_METRICS)),
            ('"GALAXY"."PRES"."ORDERS"', list(ORDERS_METRICS)),
            ('"GALAXY"."PRES"."CUSTOMERS"', list(CUSTOMERS_METRICS)),
        ]
    )
    monkeypatch.setattr(cli, "_baseline_store", lambda: store)
    monkeypatch.setattr(
        cli, "_open_session", lambda ruleset, run_id, connection_path=None: session
    )
    monkeypatch.setattr(cli, "LATEST_DIR", tmp_path / "reports")
    return store, session, tmp_path


class TestRunCommand:
    def test_both_live_run_passes_and_writes_phase_reports(self, patched, tmp_path):
        _store, _session, tmp = patched
        wb = write_twb(tmp, TWB_CUSTOM_SQL, "kpi.twb")
        out = tmp / "out"
        result = runner.invoke(
            cli.app,
            ["parity", "run", "--workbook", str(wb), "--rules", RULES, "--out", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert "snapshot phase" in result.output
        assert "check phase" in result.output
        for phase in ("snapshot", "check"):
            assert (out / phase / "report.html").exists()

    def test_blocked_snapshot_skips_check_phase(self, patched):
        _store, _session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_TWO_TABLES, "sales.twb")
        # Identity fallback off + no entries: every table is unmapped, so
        # M-MAP-001 (BLOCKER) fails statically in the snapshot phase even
        # though a session is available (nothing resolves, so the patched
        # session is never queried for these tables).
        strict_map = tmp_path / "strict.yml"
        strict_map.write_text(
            "version: 1\ndefaults: { identity_fallback: false }\n", encoding="utf-8"
        )
        result = runner.invoke(
            cli.app,
            [
                "parity", "run", "--workbook", str(wb), "--map", str(strict_map),
                "--rules", RULES,
            ],
        )
        assert result.exit_code == 2
        assert "check phase skipped" in result.output
        assert "Traceback" not in result.output

    def test_malformed_workbook_is_clean_exit_3(self, patched):
        _store, _session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_MALFORMED, "bad.twb")
        result = runner.invoke(
            cli.app, ["parity", "run", "--workbook", str(wb), "--rules", RULES]
        )
        assert result.exit_code == 3
        assert "Traceback" not in result.output


class TestPostSwapCheck:
    def _snapshot_pre_swap(self, tmp_path) -> tuple[object, object]:
        """Snapshot the pre-swap workbook, then swap it IN PLACE — the same
        file path, as Autopilot does. The snapshot prefix derives from the
        workbook filename stem, so a renamed copy would never find its
        snapshots (documented in the RUNBOOK post-swap play)."""
        wb = write_twb(tmp_path, TWB_TWO_TABLES, "sales.twb")
        map_file = tmp_path / "map.yml"
        map_file.write_text(MAP_SWAP, encoding="utf-8")
        result = runner.invoke(
            cli.app,
            [
                "parity", "snapshot", "--workbook", str(wb),
                "--map", str(map_file), "--rules", RULES,
            ],
        )
        assert result.exit_code == 0, result.output
        write_twb(tmp_path, TWB_TWO_TABLES_SWAPPED, "sales.twb")
        return wb, map_file

    def test_post_swap_check_finds_pre_swap_snapshots(self, patched_tables):
        """S8.2 AC: the swapped artifact + --post-swap verifies against the
        snapshots taken from the pre-swap workbook, same verdict."""
        _store, _session, tmp_path = patched_tables
        wb, map_file = self._snapshot_pre_swap(tmp_path)
        result = runner.invoke(
            cli.app,
            [
                "parity", "check", "--workbook", str(wb),
                "--map", str(map_file), "--rules", RULES, "--post-swap",
            ],
        )
        assert result.exit_code == 0, result.output

    def test_swapped_workbook_without_flag_blocks_with_hint(self, patched_tables):
        """S8.2 AC: forgetting --post-swap on a swapped workbook fails with
        a named suggestion (in the report evidence) instead of a confusing
        missing-snapshot wall."""
        import json

        _store, _session, tmp_path = patched_tables
        wb, map_file = self._snapshot_pre_swap(tmp_path)
        out = tmp_path / "noflag"
        result = runner.invoke(
            cli.app,
            [
                "parity", "check", "--workbook", str(wb),
                "--map", str(map_file), "--rules", RULES, "--out", str(out),
            ],
        )
        assert result.exit_code == 2
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        snap = next(c for c in report["checks"] if c["id"] == "M-SNAP-001")
        assert snap["status"] == "FAIL"
        assert "--post-swap" in snap["remediation"]
        assert "re-snapshot" in snap["remediation"].lower()

    def test_non_injective_map_fails_loud_before_session(self, patched, monkeypatch):
        """D14: post-swap with a many-to-one map is an authoring error,
        reported before any connection is opened."""
        _store, _session, tmp_path = patched
        calls: list[object] = []

        def counting_open(*args, **kwargs):
            calls.append(args)
            raise AssertionError("session must not open for a non-invertible map")

        monkeypatch.setattr(cli, "_open_session", counting_open)
        wb = write_twb(tmp_path, TWB_TWO_TABLES, "sales.twb")
        bad_map = tmp_path / "merged.yml"
        bad_map.write_text(
            "version: 1\n"
            "objects:\n"
            "  - old: LEGACY_DB.SALES.ORDERS\n"
            "    new: GALAXY.PRES.MERGED\n"
            "  - old: LEGACY_DB.CRM.CUSTOMERS\n"
            "    new: GALAXY.PRES.MERGED\n",
            encoding="utf-8",
        )
        result = runner.invoke(
            cli.app,
            [
                "parity", "check", "--workbook", str(wb),
                "--map", str(bad_map), "--rules", RULES, "--post-swap",
            ],
        )
        assert result.exit_code == 3
        assert calls == []
        assert "Traceback" not in result.output

    def test_post_swap_rejected_on_snapshot_like_phases(self, patched):
        _store, _session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        result = runner.invoke(
            cli.app,
            ["parity", "snapshot", "--workbook", str(wb), "--rules", RULES, "--post-swap"],
        )
        # snapshot has no --post-swap flag at all: typer rejects it.
        assert result.exit_code != 0


class TestEstateCommand:
    def test_glob_estate_snapshot_then_check(self, patched):
        """The wave loop (S7.3 AC): glob manifest, snapshot sweep, check
        sweep, roll-up reports written, exit 0 when every workbook READY."""
        _store, _session, tmp_path = patched
        wave = tmp_path / "wave1"
        wave.mkdir()
        write_twb(wave, TWB_CUSTOM_SQL, "kpi_a.twb")
        write_twb(wave, TWB_CUSTOM_SQL, "kpi_b.twb")
        pattern = str(wave / "*.twb")
        out = tmp_path / "out"
        snap = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", pattern, "--phase", "snapshot",
                "--rules", RULES, "--out", str(out),
            ],
        )
        assert snap.exit_code == 0, snap.output
        check = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", pattern, "--phase", "check",
                "--rules", RULES, "--out", str(out),
            ],
        )
        assert check.exit_code == 0, check.output
        assert (out / "estate.html").exists()
        assert (out / "report.html").exists()
        # The console table truncates long paths; the JUnit roll-up is the
        # machine-readable record of which workbooks ran.
        import xml.etree.ElementTree as ET

        cases = ET.parse(out / "estate.junit.xml").getroot().findall(".//testcase")
        names = " ".join(c.get("name") or "" for c in cases)
        assert len(cases) == 2
        assert "kpi_a" in names and "kpi_b" in names

    def test_estate_run_phase_does_both_sweeps(self, patched):
        _store, _session, tmp_path = patched
        wave = tmp_path / "wave1"
        wave.mkdir()
        write_twb(wave, TWB_CUSTOM_SQL, "kpi_a.twb")
        result = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", str(wave / "*.twb"),
                "--phase", "run", "--rules", RULES,
            ],
        )
        assert result.exit_code == 0, result.output

    def test_one_broken_workbook_blocks_estate_but_others_run(self, patched):
        """D17 + S7.1 AC via the CLI: the bad workbook is named, the good
        one still ran, the estate exits BLOCKED, JUnit carries both."""
        import xml.etree.ElementTree as ET

        _store, _session, tmp_path = patched
        wave = tmp_path / "wave1"
        wave.mkdir()
        write_twb(wave, TWB_CUSTOM_SQL, "good.twb")
        write_twb(wave, TWB_MALFORMED, "broken.twb")
        out = tmp_path / "out"
        result = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", str(wave / "*.twb"),
                "--phase", "snapshot", "--rules", RULES, "--out", str(out),
            ],
        )
        assert result.exit_code == 2
        assert "broken" in result.output
        assert "Traceback" not in result.output
        suite = ET.parse(out / "estate.junit.xml").getroot()
        cases = suite.findall(".//testcase")
        assert len(cases) == 2
        assert sum(1 for c in cases if c.find("error") is not None) == 1

    def test_static_only_estate_never_opens_session(self, patched, monkeypatch):
        _store, _session, tmp_path = patched

        def boom(*args, **kwargs):
            raise AssertionError("static-only must not open a session")

        monkeypatch.setattr(cli, "_open_session", boom)
        wave = tmp_path / "wave1"
        wave.mkdir()
        write_twb(wave, TWB_CUSTOM_SQL, "kpi.twb")
        result = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", str(wave / "*.twb"),
                "--phase", "snapshot", "--rules", RULES, "--static-only",
            ],
        )
        assert result.exit_code == 0, result.output

    def test_empty_glob_is_clean_exit_3(self, patched):
        _store, _session, tmp_path = patched
        result = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", str(tmp_path / "nothing" / "*.twb"),
                "--phase", "snapshot", "--rules", RULES,
            ],
        )
        assert result.exit_code == 3
        assert "Traceback" not in result.output

    def test_post_swap_rejected_outside_check_phase(self, patched):
        _store, _session, tmp_path = patched
        wave = tmp_path / "wave1"
        wave.mkdir()
        write_twb(wave, TWB_CUSTOM_SQL, "kpi.twb")
        result = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", str(wave / "*.twb"),
                "--phase", "snapshot", "--rules", RULES, "--post-swap",
            ],
        )
        assert result.exit_code == 3
        assert "check phase only" in result.output

    def test_unknown_fail_on_is_clean_exit_3(self, patched):
        _store, _session, tmp_path = patched
        wave = tmp_path / "wave1"
        wave.mkdir()
        write_twb(wave, TWB_CUSTOM_SQL, "kpi.twb")
        result = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", str(wave / "*.twb"),
                "--phase", "snapshot", "--rules", RULES, "--fail-on", "MAYBE",
            ],
        )
        assert result.exit_code == 3
        assert "fail-on" in result.output

    def test_yaml_manifest_with_per_entry_map(self, patched):
        _store, _session, tmp_path = patched
        wave = tmp_path / "wave1"
        wave.mkdir()
        write_twb(wave, TWB_CUSTOM_SQL, "kpi.twb")
        (wave / "kpi-map.yml").write_text("version: 1\n", encoding="utf-8")
        manifest = tmp_path / "wave1.yml"
        manifest.write_text(
            "version: 1\nworkbooks:\n  - path: wave1/kpi.twb\n    map: wave1/kpi-map.yml\n",
            encoding="utf-8",
        )
        result = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", str(manifest),
                "--phase", "snapshot", "--rules", RULES,
            ],
        )
        assert result.exit_code == 0, result.output


class TestQcWaveCliRegressions:
    def test_run_static_only_is_refused_not_fake_blocked(self, patched):
        """QC F18: a static snapshot phase writes nothing, so the check
        phase of `parity run --static-only` would always block on missing
        snapshots — refuse loudly (exit 3) instead of exiting 2."""
        _store, _session, tmp_path = patched
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        result = runner.invoke(
            cli.app,
            ["parity", "run", "--workbook", str(wb), "--rules", RULES, "--static-only"],
        )
        assert result.exit_code == 3
        assert "static-only" in result.output
        assert "separately" in result.output

    def test_estate_run_phase_static_only_is_refused(self, patched):
        _store, _session, tmp_path = patched
        wave = tmp_path / "wave1"
        wave.mkdir()
        write_twb(wave, TWB_CUSTOM_SQL, "kpi.twb")
        result = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", str(wave / "*.twb"),
                "--phase", "run", "--rules", RULES, "--static-only",
            ],
        )
        assert result.exit_code == 3
        assert "static-only" in result.output

    def test_estate_warns_when_rollup_and_report_verdicts_split(self, patched):
        """QC F6: with M-ESTATE-001/002 disabled in a custom ruleset the
        written reports carry compute_verdict's answer while the exit code
        follows the D17 roll-up; the split must be loudly named."""
        _store, _session, tmp_path = patched
        wave = tmp_path / "wave1"
        wave.mkdir()
        write_twb(wave, TWB_MALFORMED, "broken.twb")  # estate rollup: BLOCKED
        rules_text = Path(RULES).read_text(encoding="utf-8")
        stripped = rules_text.replace(
            "  - id: M-ESTATE-001\n    enabled: true\n", ""
        ).replace("  - id: M-ESTATE-002\n    enabled: true\n", "")
        assert "M-ESTATE" not in stripped
        no_estate_rules = tmp_path / "no-estate.yml"
        no_estate_rules.write_text(stripped, encoding="utf-8")
        result = runner.invoke(
            cli.app,
            [
                "parity", "estate", "--manifest", str(wave / "*.twb"),
                "--phase", "snapshot", "--rules", str(no_estate_rules),
            ],
        )
        # Exit code trusts the D17 roll-up (BLOCKED), not the neutered report.
        assert result.exit_code == 2
        assert "disagrees" in result.output
        assert "M-ESTATE" in result.output

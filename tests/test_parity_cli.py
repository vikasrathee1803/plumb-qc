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

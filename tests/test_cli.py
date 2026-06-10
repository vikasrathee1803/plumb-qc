"""CLI surface and exit codes via Typer's runner.

The static-only path exercises the full CLI end to end without a live
Snowflake session: load ruleset, run checks, write all three reports,
map the verdict to an exit code.
"""

from pathlib import Path

from typer.testing import CliRunner

from plumb.cli import app

runner = CliRunner()

RULES = Path(__file__).parent.parent / "rules" / "plumb.yml"


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "plumb" in result.stdout


def test_help_renders_for_every_command():
    """The rich help renderer must not crash. Regression for the Click 8.2
    make_metavar break that CliRunner command invocations did not catch."""
    for args in (
        ["--help"],
        ["check", "--help"],
        ["rules", "--help"],
        ["baseline", "--help"],
        ["report", "--help"],
        ["init", "--help"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, f"{args} failed: {result.output}"
        assert "Usage:" in result.output


def test_static_only_cartesian_join_is_blocked_exit_2(tmp_path: Path):
    out = tmp_path / "report"
    result = runner.invoke(
        app,
        [
            "check", "sql",
            "--inline", "SELECT a FROM t, u",
            "--rules", str(RULES),
            "--static-only",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 2
    assert "BLOCKED" in result.stdout
    assert (out / "report.html").exists()
    assert (out / "report.json").exists()
    assert (out / "report.junit.xml").exists()


def test_static_only_clean_query_passes_exit_0(tmp_path: Path):
    out = tmp_path / "report"
    result = runner.invoke(
        app,
        [
            "check", "sql",
            "--inline", "SELECT a, b FROM db.sch.t WHERE a > 0",
            "--rules", str(RULES),
            "--static-only",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0
    assert (out / "report.html").exists()


def test_malformed_ruleset_exits_3_with_clear_message(tmp_path: Path):
    bad = tmp_path / "bad.yml"
    bad.write_text("version: '1'\ndefaults:\n  fail_on: NONSENSE\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["check", "sql", "--inline", "SELECT 1", "--rules", str(bad), "--static-only"],
    )
    assert result.exit_code == 3
    assert "fail_on" in result.output


def test_missing_sql_input_exits_3():
    result = runner.invoke(app, ["check", "sql", "--rules", str(RULES), "--static-only"])
    assert result.exit_code == 3


def test_unsupported_kind_exits_3():
    result = runner.invoke(app, ["check", "tableau", "--inline", "x", "--static-only"])
    assert result.exit_code == 3


def test_rules_pin_and_show(tmp_path: Path, monkeypatch):
    pin = tmp_path / "rules.pin"
    monkeypatch.setattr("plumb.config.loader.PIN_FILE", pin)
    result = runner.invoke(app, ["rules", "pin", "2026.06.0"])
    assert result.exit_code == 0
    show = runner.invoke(app, ["rules", "show"])
    assert "2026.06.0" in show.stdout


def test_cli_threads_run_id_into_query_tag(tmp_path: Path, monkeypatch):
    """Regression: the session must be opened with the run's real run_id so
    QUERY_TAG is plumb_qc:{run_id}, not plumb_qc:pending. Found via live
    QUERY_HISTORY."""
    import json

    from plumb import cli
    from plumb.connect.snowflake import build_query_tag
    from tests._fakes import RouteSession

    captured: dict = {}

    def fake_open(ruleset, run_id):
        captured["run_id"] = run_id
        session = RouteSession()
        session.query_tag = build_query_tag(run_id)
        return session

    monkeypatch.setattr(cli, "_open_session", fake_open)
    out = tmp_path / "r"
    runner.invoke(
        app,
        ["check", "sql", "--inline", "SELECT a FROM t", "--rules", str(RULES), "--out", str(out)],
    )
    report = json.loads((out / "report.json").read_text())
    assert report["environment"]["query_tag"] == f"plumb_qc:{report['run_id']}"
    assert report["environment"]["query_tag"] != "plumb_qc:pending"
    assert captured["run_id"] == report["run_id"]


def test_report_open_without_report_exits_3(tmp_path: Path):
    result = runner.invoke(app, ["report", "open", "--path", str(tmp_path)])
    assert result.exit_code == 3


def test_query_file_with_utf8_bom_is_accepted(tmp_path: Path):
    """Cycle-2 fix: PowerShell, SSMS, and VS Code routinely write .sql files
    with a UTF-8 BOM; the BOM must never reach the SQL parser (it produced a
    baffling 'Invalid expression' exit 3 on a perfectly good query)."""
    query = tmp_path / "bom.sql"
    query.write_bytes(b"\xef\xbb\xbf" + b"SELECT a, b FROM db.sch.t WHERE a > 0")
    result = runner.invoke(
        app,
        ["check", "sql", "--query", str(query), "--rules", str(RULES), "--static-only"],
    )
    assert result.exit_code == 0, result.output

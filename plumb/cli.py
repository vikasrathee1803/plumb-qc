"""Typer entrypoint. The exit codes are the CI contract:

0  verdict passes the fail_on gate
1  REVIEW (or READY_WITH_NOTES when fail_on is READY_WITH_NOTES)
2  BLOCKED
3  tool, configuration, or connection error
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from plumb import __version__
from plumb.baseline.store import LocalParquetStore, make_baseline
from plumb.checks._sql import SqlParseError, select_all_query
from plumb.checks._tableau import TableauParseError, parse_workbook
from plumb.config.loader import (
    CONNECTION_FILE,
    PLUMB_HOME,
    ConfigError,
    load_connection_profile,
    load_profile,
    load_ruleset,
    read_pin,
    resolve_profile,
    write_pin,
)
from plumb.config.models import Ruleset
from plumb.connect.snowflake import (
    AuthConfigError,
    ReadOnlyViolation,
    SnowflakeConnectError,
    SnowflakeSession,
)
from plumb.engine.audit import write_audit_record
from plumb.engine.models import RunResult, Status, Target, Verdict
from plumb.engine.runner import RunRequest, run_checks
from plumb.engine.verdict import coverage_caption
from plumb.report.html import write_html
from plumb.report.json_out import write_json
from plumb.report.junit import write_junit

EXIT_PASSING = 0
EXIT_REVIEW = 1
EXIT_BLOCKED = 2
EXIT_TOOL_ERROR = 3

REPORTS_HOME = PLUMB_HOME / "reports"
LATEST_DIR = REPORTS_HOME / "latest"
RULES_HOME = PLUMB_HOME / "rules"

_VERDICT_RANK = {
    Verdict.BLOCKED: 0,
    Verdict.REVIEW: 1,
    Verdict.READY_WITH_NOTES: 2,
    Verdict.READY: 3,
}

_VERDICT_STYLE = {
    Verdict.BLOCKED: "bold white on red",
    Verdict.REVIEW: "bold white on dark_orange",
    Verdict.READY_WITH_NOTES: "bold white on blue",
    Verdict.READY: "bold white on green",
}

_STATUS_STYLE = {
    Status.PASS: "green",
    Status.FAIL: "red",
    Status.WARN: "dark_orange",
    Status.SKIP: "grey50",
    Status.ERROR: "magenta",
}


def exit_code_for_verdict(verdict: Verdict, fail_on: str) -> int:
    """Map a verdict to the CI exit code, honoring the ruleset gate.

    A verdict fails the gate when it is at or below fail_on in the rank
    order BLOCKED < REVIEW < READY_WITH_NOTES < READY. BLOCKED is always
    exit 2. Anything else that fails the gate is exit 1. See ADR-0005.
    """
    if verdict is Verdict.BLOCKED:
        return EXIT_BLOCKED
    gate = Verdict(fail_on)
    if _VERDICT_RANK[verdict] <= _VERDICT_RANK[gate]:
        return EXIT_REVIEW
    return EXIT_PASSING


console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="plumb",
    help="QC and confidence engine for Snowflake SQL and Tableau builds.",
    no_args_is_help=True,
)
rules_app = typer.Typer(help="Manage the central, versioned ruleset.")
baseline_app = typer.Typer(help="Manage golden baselines for regression diff.")
report_app = typer.Typer(help="Work with generated reports.")
app.add_typer(rules_app, name="rules")
app.add_typer(baseline_app, name="baseline")
app.add_typer(report_app, name="report")


def _fail(message: str) -> "typer.Exit":
    err_console.print(f"[red]error:[/red] {message}")
    return typer.Exit(EXIT_TOOL_ERROR)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"plumb {__version__}")
        raise typer.Exit(EXIT_PASSING)


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show the plumb version and exit.",
    ),
) -> None:
    """Plumb proves a BI build is correct before it ships."""


# --- rules --------------------------------------------------------------

@rules_app.command("pin")
def rules_pin(version: str = typer.Argument(..., help="Ruleset version to pin.")) -> None:
    """Pin the active ruleset version so every run checks the same standard."""
    try:
        write_pin(version)
    except ConfigError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"pinned ruleset version [bold]{version}[/bold]")


@rules_app.command("show")
def rules_show() -> None:
    """Show the currently pinned ruleset version."""
    pinned = read_pin()
    console.print(
        f"pinned ruleset version: [bold]{pinned}[/bold]" if pinned
        else "no ruleset version is pinned"
    )


@rules_app.command("pull")
def rules_pull(
    source: Path = typer.Option(
        None, "--source", help="Path to a rules repo checkout to copy from."
    ),
) -> None:
    """Fetch the central ruleset into ~/.plumb/rules.

    Transport to the org's plumb-rules git repo is configured per site; by
    default this copies from a local --source checkout, or seeds from the
    bundled default ruleset if none is given."""
    RULES_HOME.mkdir(parents=True, exist_ok=True)
    origin = source or _repo_rules_dir()
    if origin is None or not origin.exists():
        raise _fail(
            "no rules source found; pass --source pointing at a plumb-rules checkout"
        )
    shutil.copytree(origin, RULES_HOME, dirs_exist_ok=True)
    console.print(f"pulled ruleset from [bold]{origin}[/bold] into {RULES_HOME}")


# --- init ---------------------------------------------------------------

@app.command("init")
def init() -> None:
    """Scaffold a connection profile and a sample check spec."""
    PLUMB_HOME.mkdir(parents=True, exist_ok=True)
    if CONNECTION_FILE.exists():
        console.print(f"connection profile already exists at {CONNECTION_FILE}")
    else:
        CONNECTION_FILE.write_text(_SAMPLE_CONNECTION, encoding="utf-8")
        console.print(f"wrote sample connection profile to {CONNECTION_FILE}")
    sample_spec = Path("plumb_sample_check.yml")
    if not sample_spec.exists():
        sample_spec.write_text(_SAMPLE_SPEC, encoding="utf-8")
        console.print(f"wrote a sample check spec to {sample_spec}")
    console.print("next: edit the connection profile, then run 'plumb rules pull'")


# --- check --------------------------------------------------------------

@app.command("check")
def check(
    kind: str = typer.Argument(..., help="What to check: 'sql' or 'tableau'."),
    query: Path = typer.Option(None, "--query", help="Path to a .sql file."),
    inline: str = typer.Option(None, "--inline", help="Inline SQL string."),
    workbook: Path = typer.Option(None, "--workbook", help="Path to a .twb or .twbx."),
    profile: str = typer.Option(None, "--profile", help="Profile name to apply."),
    baseline: str = typer.Option(None, "--baseline", help="Baseline name for regression diff."),
    rules: Path = typer.Option(None, "--rules", help="Path to a ruleset file."),
    out: Path = typer.Option(None, "--out", help="Report output directory."),
    static_only: bool = typer.Option(
        False, "--static-only", help="Skip the Snowflake connection; run static checks only."
    ),
    explain: bool = typer.Option(
        False, "--explain", help="Attach AI explanations to failures (Phase 2)."
    ),
) -> None:
    """Run the checks and write the HTML, JSON, and JUnit reports."""
    if kind not in ("sql", "tableau"):
        raise _fail(f"unsupported check kind {kind!r}; use 'sql' or 'tableau'")
    if explain:
        err_console.print(
            "[yellow]note:[/yellow] --explain is a Phase 2 feature and does not "
            "alter any verdict; ignoring for now."
        )

    ruleset = _resolve_ruleset(rules, profile)
    session = None
    request_kwargs: dict = {}

    if kind == "tableau":
        target, parsed = _load_workbook(workbook)
        request_kwargs["workbook"] = parsed
    else:
        sql_text, target = _load_target(query, inline)
        request_kwargs["sql_text"] = sql_text
        request_kwargs["baseline_store"] = LocalParquetStore()
        request_kwargs["baseline_name"] = baseline
        if not static_only:
            session = _open_session(ruleset)
            request_kwargs["session"] = session

    try:
        request = RunRequest(
            target=target, ruleset=ruleset, profile=profile, **request_kwargs
        )
        result = run_checks(request)
    finally:
        if session is not None:
            session.close()

    out_dir = out or LATEST_DIR
    _write_reports(result, out_dir)
    try:
        write_audit_record(result)
    except OSError as exc:
        err_console.print(f"[yellow]warning:[/yellow] could not write audit record: {exc}")

    _print_summary(result, out_dir)
    raise typer.Exit(exit_code_for_verdict(result.verdict, ruleset.defaults.fail_on))


# --- baseline -----------------------------------------------------------

@baseline_app.command("create")
def baseline_create(
    name: str = typer.Option(..., "--name", help="Baseline name."),
    query: Path = typer.Option(..., "--query", help="Path to the .sql file."),
    profile: str = typer.Option(None, "--profile", help="Profile name to apply."),
    rules: Path = typer.Option(None, "--rules", help="Path to a ruleset file."),
) -> None:
    """Capture a golden baseline from the current query output."""
    _do_baseline(name, query, profile, rules, verb="created")


@baseline_app.command("update")
def baseline_update(
    name: str = typer.Option(..., "--name", help="Baseline name."),
    query: Path = typer.Option(..., "--query", help="Path to the .sql file."),
    profile: str = typer.Option(None, "--profile", help="Profile name to apply."),
    rules: Path = typer.Option(None, "--rules", help="Path to a ruleset file."),
) -> None:
    """Refresh an existing baseline from the current query output."""
    _do_baseline(name, query, profile, rules, verb="updated")


@baseline_app.command("list")
def baseline_list() -> None:
    """List saved baselines."""
    names = LocalParquetStore().list_names()
    if not names:
        console.print("no baselines saved")
        return
    for name in names:
        console.print(f"  {name}")


# --- report -------------------------------------------------------------

@report_app.command("open")
def report_open(
    path: Path = typer.Option(None, "--path", help="Report directory to open."),
) -> None:
    """Open the most recent HTML report."""
    report_dir = path or LATEST_DIR
    html = report_dir / "report.html"
    if not html.exists():
        raise _fail(f"no report found at {html}; run 'plumb check sql' first")
    typer.launch(str(html))
    console.print(f"opened {html}")


# --- helpers ------------------------------------------------------------

def _load_target(query: Path | None, inline: str | None) -> tuple[str, Target]:
    if query and inline:
        raise _fail("pass only one of --query or --inline")
    if query:
        if not query.exists():
            raise _fail(f"query file not found: {query}")
        return query.read_text(encoding="utf-8"), Target(
            type="sql", name=query.stem, source_ref=str(query)
        )
    if inline:
        return inline, Target(type="sql", name="inline", source_ref=None)
    raise _fail("provide SQL with --query PATH or --inline 'SELECT ...'")


def _load_workbook(workbook: Path | None) -> tuple[Target, object]:
    if not workbook:
        raise _fail("provide a workbook with --workbook PATH (.twb or .twbx)")
    if not workbook.exists():
        raise _fail(f"workbook not found: {workbook}")
    try:
        parsed = parse_workbook(workbook)
    except TableauParseError as exc:
        raise _fail(str(exc)) from exc
    target = Target(type="tableau", name=workbook.stem, source_ref=str(workbook))
    return target, parsed


def _repo_rules_dir() -> Path | None:
    candidate = Path(__file__).resolve().parent.parent / "rules"
    return candidate if candidate.exists() else None


def _resolve_ruleset_path(explicit: Path | None) -> Path:
    if explicit:
        if not explicit.exists():
            raise _fail(f"ruleset file not found: {explicit}")
        return explicit
    for candidate in (RULES_HOME / "plumb.yml", Path("rules") / "plumb.yml"):
        if candidate.exists():
            return candidate
    repo = _repo_rules_dir()
    if repo and (repo / "plumb.yml").exists():
        return repo / "plumb.yml"
    raise _fail(
        "no ruleset found; run 'plumb rules pull' or pass --rules PATH"
    )


def _resolve_profile_path(name: str) -> Path:
    for base in (RULES_HOME / "profiles", Path("rules") / "profiles"):
        candidate = base / f"{name}.yml"
        if candidate.exists():
            return candidate
    repo = _repo_rules_dir()
    if repo and (repo / "profiles" / f"{name}.yml").exists():
        return repo / "profiles" / f"{name}.yml"
    raise _fail(f"profile {name!r} not found in rules/profiles")


def _resolve_ruleset(rules: Path | None, profile: str | None) -> Ruleset:
    try:
        ruleset = load_ruleset(_resolve_ruleset_path(rules))
        if profile:
            ruleset = resolve_profile(ruleset, load_profile(_resolve_profile_path(profile)))
    except ConfigError as exc:
        raise _fail(str(exc)) from exc
    return ruleset


def _open_session(ruleset: Ruleset) -> SnowflakeSession:
    try:
        connection = load_connection_profile()
    except ConfigError as exc:
        raise _fail(
            f"{exc}\nrun 'plumb init' then edit {CONNECTION_FILE}, "
            f"or use --static-only to run without Snowflake"
        ) from exc
    session = SnowflakeSession(
        connection,
        run_id="pending",
        statement_timeout_s=ruleset.defaults.statement_timeout_s,
        max_result_rows=ruleset.defaults.max_result_rows,
    )
    try:
        return session.open()
    except (AuthConfigError, SnowflakeConnectError, ReadOnlyViolation) as exc:
        raise _fail(str(exc)) from exc


def _do_baseline(
    name: str, query: Path, profile: str | None, rules: Path | None, *, verb: str
) -> None:
    if not query.exists():
        raise _fail(f"query file not found: {query}")
    sql_text = query.read_text(encoding="utf-8")
    ruleset = _resolve_ruleset(rules, profile)
    session = _open_session(ruleset)
    try:
        capped = select_all_query(sql_text, ruleset.defaults.max_result_rows)
        result = session.execute(capped)
    except SqlParseError as exc:
        raise _fail(f"could not parse query: {exc}") from exc
    except (ReadOnlyViolation, SnowflakeConnectError) as exc:
        raise _fail(str(exc)) from exc
    finally:
        session.close()
    columns = list(result.rows[0].keys()) if result.rows else []
    baseline = make_baseline(
        name, columns, result.rows, source_ref=str(query), ruleset_version=ruleset.version
    )
    LocalParquetStore().save(baseline)
    console.print(f"{verb} baseline [bold]{name}[/bold] ({baseline.row_count} rows)")


def _write_reports(result: RunResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_html(result, out_dir / "report.html")
    write_json(result, out_dir / "report.json")
    write_junit(result, out_dir / "report.junit.xml")


def _print_summary(result: RunResult, out_dir: Path) -> None:
    style = _VERDICT_STYLE[result.verdict]
    caption = coverage_caption(result.coverage)
    console.print()
    console.print(f"[{style}] {result.verdict.value} [/{style}]  {caption}")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Observed")
    for c in result.checks:
        if c.status is Status.PASS:
            continue
        st = _STATUS_STYLE[c.status]
        table.add_row(
            f"{c.id} ({c.severity.value})",
            f"[{st}]{c.status.value}[/{st}]",
            c.observed or "",
        )
    if table.row_count:
        console.print(table)
    s = result.summary
    console.print(
        f"passed {s.passed} | blocker {s.blocker} | high {s.high} | medium {s.medium} "
        f"| low {s.low} | warned {s.warned} | errored {s.errored} | skipped {s.skipped}"
    )
    console.print(f"reports written to {out_dir}")


_SAMPLE_CONNECTION = """\
account: "myorg-account"
user: "YOUR_USER"
authenticator: "externalbrowser"   # or snowflake_jwt (with private_key_path) or oauth
# private_key_path: "~/.plumb/keys/plumb_rsa_key.p8"
role: "PLUMB_QC_ROLE"
warehouse: "PLUMB_WH"
"""

_SAMPLE_SPEC = """\
# A per-query check spec example. Enable the data assertions your build needs.
# Run: plumb check sql --query your_query.sql --profile finance
checks:
  - id: D-GRAIN-001
    enabled: true
    params: { key: ["order_id"] }
  - id: D-FRESH-001
    enabled: true
    params: { event_ts_col: "created_at", sla_hours: 24 }
"""


def main() -> None:
    app()

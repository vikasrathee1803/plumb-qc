"""Typer entrypoint. The exit codes are the CI contract:

0  verdict passes the fail_on gate
1  REVIEW (or READY_WITH_NOTES when fail_on is READY_WITH_NOTES)
2  BLOCKED
3  tool, configuration, or connection error

Phase 0 ships the skeleton plus 'plumb rules pin'. The check, baseline,
init, and report commands land in Phase 1 per the build sequence.
"""

from __future__ import annotations

import typer
from rich.console import Console

from plumb import __version__
from plumb.config.loader import ConfigError, read_pin, write_pin
from plumb.engine.models import Verdict

EXIT_PASSING = 0
EXIT_REVIEW = 1
EXIT_BLOCKED = 2
EXIT_TOOL_ERROR = 3

_VERDICT_RANK = {
    Verdict.BLOCKED: 0,
    Verdict.REVIEW: 1,
    Verdict.READY_WITH_NOTES: 2,
    Verdict.READY: 3,
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
app.add_typer(rules_app, name="rules")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"plumb {__version__}")
        raise typer.Exit(EXIT_PASSING)


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the plumb version and exit.",
    ),
) -> None:
    """Plumb proves a BI build is correct before it ships."""


@rules_app.command("pin")
def rules_pin(version: str = typer.Argument(..., help="Ruleset version to pin.")) -> None:
    """Pin the active ruleset version so every run checks the same standard."""
    try:
        write_pin(version)
    except ConfigError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(EXIT_TOOL_ERROR) from exc
    console.print(f"pinned ruleset version [bold]{version}[/bold]")


@rules_app.command("show")
def rules_show() -> None:
    """Show the currently pinned ruleset version."""
    pinned = read_pin()
    if pinned is None:
        console.print("no ruleset version is pinned")
    else:
        console.print(f"pinned ruleset version: [bold]{pinned}[/bold]")


def main() -> None:
    app()

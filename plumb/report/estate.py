"""Estate roll-up report writers (PARITY-PLAN-V2 S7.3).

One HTML page and one JUnit file per estate run, consuming only the
EstateResult - same stance as report/html.py and report/junit.py: the
writers render what the runner decided, they decide nothing. The unit
here is the WORKBOOK, not the check: CI renders one test case per
workbook so every blocked or errored workbook is a named red row in the
wave's gate, and the HTML table answers "which workbook is holding the
wave" at a glance. Autoescaping is on (HTML); for the XML, ElementTree
escapes markup but NOT the control characters XML 1.0 forbids, so every
attribute and text node built from workbook paths or error text goes
through xml_safe (QC F5).
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from jinja2 import Environment, FileSystemLoader, select_autoescape

from plumb import __version__
from plumb.engine.models import Verdict
from plumb.parity.contracts import EstateResult, WorkbookParity
from plumb.report._xml import xml_safe

# Same design system as report.html.j2: the estate roll-up is the same
# product surface as the per-run confidence report, and must look like it
# (user finding: the original ad-hoc inline template read as a different
# app next to report.html).
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "estate.html.j2"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)

def _row(entry: WorkbookParity) -> dict[str, str]:
    # An errored entry counts as BLOCKED in compute_rollup; its Worst pill
    # must never show a phase verdict that happened to succeed (a green
    # READY next to red error text — QC F4). ERROR wins the cell.
    if entry.error is not None:
        worst = "ERROR"
    else:
        worst = entry.verdict.value if entry.verdict else ""
    return {
        "path": entry.workbook_path,
        "map": entry.map_path or "",
        "snapshot": entry.snapshot_result.verdict.value if entry.snapshot_result else "",
        "check": entry.check_result.verdict.value if entry.check_result else "",
        "worst": worst,
        "error": entry.error or "",
    }


def _counts(estate: EstateResult) -> dict[str, int]:
    counts = {
        "total": len(estate.entries),
        "blocked": 0,
        "review": 0,
        "notes": 0,
        "ready": 0,
        "errored": 0,
    }
    buckets = {
        Verdict.BLOCKED: "blocked",
        Verdict.REVIEW: "review",
        Verdict.READY_WITH_NOTES: "notes",
        Verdict.READY: "ready",
    }
    for entry in estate.entries:
        if entry.error is not None:
            counts["errored"] += 1
        elif entry.verdict is None:
            # Never ran and never errored; compute_rollup treats this as
            # BLOCKED, so the header counts must agree.
            counts["blocked"] += 1
        else:
            counts[buckets[entry.verdict]] += 1
    return counts


def render_estate_html(estate: EstateResult) -> str:
    rollup = estate.rollup.value if estate.rollup is not None else "NONE"
    rollup_label = (
        estate.rollup.value.replace("_", " ").title()
        if estate.rollup is not None
        else "No Roll-Up"
    )
    return _env.get_template(_TEMPLATE_NAME).render(
        estate=estate,
        rollup=rollup,
        rollup_label=rollup_label,
        counts=_counts(estate),
        rows=[_row(entry) for entry in estate.entries],
        plumb_version=__version__,
    )


def write_estate_html(estate: EstateResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_estate_html(estate), encoding="utf-8")


def render_estate_junit(estate: EstateResult) -> str:
    failures = 0
    errors = 0
    for entry in estate.entries:
        if entry.error is not None:
            errors += 1
        elif entry.verdict in (Verdict.BLOCKED, Verdict.REVIEW) or entry.verdict is None:
            failures += 1
    suite = ET.Element(
        "testsuite",
        {
            "name": "plumb-parity-estate",
            "tests": str(len(estate.entries)),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": "0",
        },
    )
    properties = ET.SubElement(suite, "properties")
    for key, value in (
        ("phase", estate.phase),
        ("rollup", estate.rollup.value if estate.rollup is not None else ""),
        ("manifest", estate.manifest_ref or ""),
        ("created_at", estate.created_at),
    ):
        ET.SubElement(properties, "property", {"name": key, "value": xml_safe(value)})

    for entry in estate.entries:
        case = ET.SubElement(
            suite,
            "testcase",
            {"classname": "plumb-parity-estate", "name": xml_safe(entry.workbook_path)},
        )
        verdict = entry.verdict
        if entry.error is not None:
            ET.SubElement(case, "error", {"message": xml_safe(entry.error)})
        elif verdict is None:
            # No error recorded but no phase produced a result either; the
            # roll-up counts this as BLOCKED, so the CI row is red too.
            ET.SubElement(case, "failure", {"message": "verdict: none (no result)"})
        elif verdict in (Verdict.BLOCKED, Verdict.REVIEW):
            ET.SubElement(case, "failure", {"message": f"verdict: {verdict.value}"})
        elif verdict is Verdict.READY_WITH_NOTES:
            # Passing testcase with the verdict noted: notes never fail the
            # gate, but they must stay visible in the CI log.
            out = ET.SubElement(case, "system-out")
            out.text = f"verdict: {verdict.value}"

    ET.indent(suite)
    return ET.tostring(suite, encoding="unicode", xml_declaration=True)


def write_estate_junit(estate: EstateResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_estate_junit(estate), encoding="utf-8")

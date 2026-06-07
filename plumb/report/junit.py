"""JUnit XML writer for CI renderers.

One testcase per check. FAIL maps to failure, ERROR to error, SKIP to
skipped. WARN is a passing testcase with a note, because a WARN never
fails the verdict; the CI gate is the process exit code, not this file.
ElementTree handles XML escaping so check text is safe.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from plumb.engine.models import RunResult, Status


def render_junit(result: RunResult) -> str:
    summary = result.summary
    failures = summary.blocker + summary.high + summary.medium + summary.low + summary.info
    suite = ET.Element(
        "testsuite",
        {
            "name": f"plumb:{result.target.name}",
            "tests": str(summary.total),
            "failures": str(failures),
            "errors": str(summary.errored),
            "skipped": str(summary.skipped),
        },
    )
    properties = ET.SubElement(suite, "properties")
    for key, value in (
        ("verdict", result.verdict.value),
        ("ruleset_version", result.ruleset_version),
        ("profile", result.profile or ""),
        ("run_id", result.run_id),
    ):
        ET.SubElement(properties, "property", {"name": key, "value": value})

    for check in result.checks:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"{result.target.name}.{check.family.value}",
                "name": f"{check.id} {check.name}",
            },
        )
        message = _message(check)
        if check.status is Status.FAIL:
            node = ET.SubElement(
                case, "failure", {"type": check.severity.value, "message": message}
            )
            node.text = check.remediation or ""
        elif check.status is Status.ERROR:
            node = ET.SubElement(case, "error", {"message": message})
            node.text = check.remediation or ""
        elif check.status is Status.SKIP:
            ET.SubElement(case, "skipped", {"message": message})
        elif check.status is Status.WARN:
            out = ET.SubElement(case, "system-out")
            out.text = f"WARN: {message}"

    ET.indent(suite)
    return ET.tostring(suite, encoding="unicode", xml_declaration=True)


def _message(check) -> str:  # type: ignore[no-untyped-def]
    parts = []
    if check.observed:
        parts.append(f"observed: {check.observed}")
    if check.expected:
        parts.append(f"expected: {check.expected}")
    return " | ".join(parts) or check.name


def write_junit(result: RunResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_junit(result), encoding="utf-8")
    return path

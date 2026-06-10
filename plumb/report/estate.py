"""Estate roll-up report writers (PARITY-PLAN-V2 S7.3).

One HTML page and one JUnit file per estate run, consuming only the
EstateResult - same stance as report/html.py and report/junit.py: the
writers render what the runner decided, they decide nothing. The unit
here is the WORKBOOK, not the check: CI renders one test case per
workbook so every blocked or errored workbook is a named red row in the
wave's gate, and the HTML table answers "which workbook is holding the
wave" at a glance. Autoescaping is on (HTML) and ElementTree escapes the
XML, so workbook paths and error text cannot break either report.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from jinja2 import Environment

from plumb import __version__
from plumb.engine.models import Verdict
from plumb.parity.contracts import EstateResult, WorkbookParity

_env = Environment(autoescape=True)

_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plumb estate roll-up</title>
<style>
  body { margin:0; font-family:ui-sans-serif,system-ui,sans-serif; background:#07080c;
    color:#ecedf3; font-size:14px; line-height:1.5; }
  .shell { max-width:1100px; margin:0 auto; padding:28px 24px 60px; }
  .top { display:flex; align-items:baseline; gap:10px; margin-bottom:18px; }
  .logo { font-size:19px; font-weight:700; } .logo .dot { color:#7c6bff; }
  .top .tag { color:#757a8c; font-size:12px; }
  .card { background:#0f1117; border:1px solid #1d2030; border-radius:14px;
    padding:18px; margin-bottom:16px; }
  .vbig { font-size:28px; font-weight:800; }
  .meta { color:#a0a6b5; font-size:13px; margin-top:4px; }
  .meta b { color:#ecedf3; }
  .v-BLOCKED { color:#ff6b7d; } .v-REVIEW { color:#f5a524; }
  .v-READY_WITH_NOTES { color:#5aa9ff; } .v-READY { color:#3ddc97; }
  .v-NONE { color:#757a8c; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid #1d2030;
    vertical-align:top; }
  th { color:#757a8c; font-size:11px; text-transform:uppercase; letter-spacing:0.05em; }
  td.mono { font-family:ui-monospace,monospace; font-size:12px; color:#c4c8d3; }
  .pill { display:inline-block; font-size:11px; font-weight:700; padding:2px 9px;
    border-radius:999px; }
  .p-BLOCKED { background:rgba(255,107,125,0.12); color:#ff6b7d; }
  .p-REVIEW { background:rgba(245,165,36,0.12); color:#f5a524; }
  .p-READY_WITH_NOTES { background:rgba(90,169,255,0.12); color:#5aa9ff; }
  .p-READY { background:rgba(61,220,151,0.12); color:#3ddc97; }
  .p-NONE { background:#1a1d28; color:#757a8c; }
  .err { color:#ff6b7d; font-size:12px; font-family:ui-monospace,monospace; }
  footer { color:#757a8c; font-size:12px; text-align:center; padding-top:8px; }
</style>
</head>
<body>
<div class="shell">
  <div class="top">
    <span class="logo">plumb<span class="dot">.</span></span>
    <span class="tag">estate roll-up</span>
  </div>
  <div class="card">
    <div class="vbig v-{{ rollup }}">{{ rollup_label }}</div>
    <div class="meta">
      phase <b>{{ estate.phase }}</b>
      {% if estate.manifest_ref %} &middot; manifest <b>{{ estate.manifest_ref }}</b>{% endif %}
      &middot; {{ estate.created_at }}
    </div>
    <div class="meta">
      <b>{{ counts.total }}</b> workbooks &middot;
      {{ counts.blocked }} blocked &middot; {{ counts.review }} review &middot;
      {{ counts.notes }} ready with notes &middot; {{ counts.ready }} ready &middot;
      {{ counts.errored }} errored
    </div>
  </div>
  <div class="card">
    <table>
      <thead>
        <tr>
          <th>Workbook</th><th>Map</th><th>Snapshot</th>
          <th>Check</th><th>Worst</th><th>Error</th>
        </tr>
      </thead>
      <tbody>
      {% for row in rows %}
        <tr>
          <td class="mono">{{ row.path }}</td>
          <td class="mono">{{ row.map }}</td>
          <td><span class="pill p-{{ row.snapshot or 'NONE' }}">{{ row.snapshot or '-' }}\
</span></td>
          <td><span class="pill p-{{ row.check or 'NONE' }}">{{ row.check or '-' }}\
</span></td>
          <td><span class="pill p-{{ row.worst or 'NONE' }}">{{ row.worst or '-' }}\
</span></td>
          <td class="err">{{ row.error }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  <footer>
    Plumb {{ plumb_version }} &middot; deterministic verdict, no AI in the verdict path
  </footer>
</div>
</body>
</html>
"""

_template = _env.from_string(_TEMPLATE)


def _row(entry: WorkbookParity) -> dict[str, str]:
    return {
        "path": entry.workbook_path,
        "map": entry.map_path or "",
        "snapshot": entry.snapshot_result.verdict.value if entry.snapshot_result else "",
        "check": entry.check_result.verdict.value if entry.check_result else "",
        "worst": entry.verdict.value if entry.verdict else "",
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
    return _template.render(
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
        ET.SubElement(properties, "property", {"name": key, "value": value})

    for entry in estate.entries:
        case = ET.SubElement(
            suite,
            "testcase",
            {"classname": "plumb-parity-estate", "name": entry.workbook_path},
        )
        verdict = entry.verdict
        if entry.error is not None:
            ET.SubElement(case, "error", {"message": entry.error})
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

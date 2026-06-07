"""HTML writer: one self-contained file, inline CSS, no external assets.

Consumes only the RunResult, so it is identical whether driven by the CLI
or the Phase 2 web UI. Autoescaping is on, so evidence values cannot break
the page or inject markup.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from plumb import __version__
from plumb.engine.models import RunResult

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "report.html.j2"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


def render_html(result: RunResult) -> str:
    template = _env.get_template(_TEMPLATE_NAME)
    return template.render(result=result, plumb_version=__version__)


def write_html(result: RunResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(result), encoding="utf-8")
    return path

"""plumb doctor must judge the launch path the CLI actually uses.

Cycle-2 regression: web/ is a repo-root sibling of the plumb package and is
not part of the wheel; the `plumb web` command inserts the repo root on
sys.path before importing it. The doctor previously imported `web.api`
without that insertion and false-FAILed a healthy editable install — the
worst failure mode for a self-check, because it teaches users to ignore it.
"""

from __future__ import annotations

from pathlib import Path

import plumb.diagnostics as diagnostics


def test_doctor_passes_on_this_source_checkout():
    """Every row PASSes here: the venv has all runtime deps, the repo root
    carries web/, and the engine runs. This is the exact scenario that
    previously reported two FAILs."""
    rows = diagnostics.diagnose()
    failures = [(label, detail) for label, ok, detail in rows if not ok]
    assert failures == []


def test_web_rows_degrade_to_note_when_web_absent(monkeypatch, tmp_path: Path):
    """A wheel install has no web/ directory at all: the web rows must
    report the fact as a note, never as a FAIL on a working install."""
    monkeypatch.setattr(diagnostics, "_ROOT", tmp_path)
    assert diagnostics._web_app_builds() == diagnostics._WEB_ABSENT
    assert diagnostics._web_ui_built() == diagnostics._WEB_ABSENT


def test_dist_missing_with_web_present_still_fails(monkeypatch, tmp_path: Path):
    """The honest FAIL stays: a source checkout whose UI was never built is
    a real gap (the web command would 404 the SPA)."""
    (tmp_path / "web").mkdir()
    monkeypatch.setattr(diagnostics, "_ROOT", tmp_path)
    monkeypatch.setattr(
        diagnostics, "_DIST", tmp_path / "web" / "ui" / "dist" / "index.html"
    )
    try:
        diagnostics._web_ui_built()
    except FileNotFoundError as exc:
        assert "npm run build" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError for a missing dist")

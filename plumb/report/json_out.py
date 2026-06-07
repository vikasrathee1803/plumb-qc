"""JSON writer. Emits the RunResult exactly as the machine-readable
contract in PLUMB_SPEC.md, so any consumer parses one stable shape."""

from __future__ import annotations

from pathlib import Path

from plumb.engine.models import RunResult


def render_json(result: RunResult) -> str:
    return result.model_dump_json(indent=2)


def write_json(result: RunResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_json(result), encoding="utf-8")
    return path

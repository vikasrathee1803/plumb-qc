"""Local audit trail: one JSON line per run.

Every run appends who, when, target, ruleset version, and verdict to a
JSON-lines file. The location is overridable with PLUMB_AUDIT_FILE so an
enterprise can point it at a monitored path that ships to a central SIEM
(the recommended way to make the trail centralized and tamper-evident); the
default is local. No secret or evidence value is ever recorded here.
"""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path

from plumb.engine.models import RunResult, utc_now

AUDIT_HOME = Path.home() / ".plumb"
AUDIT_FILE = Path(os.environ.get("PLUMB_AUDIT_FILE") or (AUDIT_HOME / "audit.jsonl"))


def audit_record(result: RunResult, *, user: str | None = None) -> dict[str, object]:
    return {
        "run_id": result.run_id,
        "timestamp": utc_now().isoformat(),
        "user": user or _current_user(),
        "target_type": result.target.type,
        "target_name": result.target.name,
        "source_ref": result.target.source_ref,
        "ruleset_version": result.ruleset_version,
        "profile": result.profile,
        "verdict": result.verdict.value,
    }


def write_audit_record(
    result: RunResult, path: Path | None = None, *, user: str | None = None
) -> Path:
    target = path or AUDIT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(audit_record(result, user=user), default=str)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return target


def _current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - audit must never crash a run
        return "unknown"

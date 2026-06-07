"""Core engine contracts: CheckResult and RunResult.

These models are the single source of truth that every check family, every
report writer, the CLI, the Phase 2 web UI, and the AI assist layer build
against. They mirror the JSON run-result contract in PLUMB_SPEC.md exactly.
Changing a field here is a breaking contract change and needs an ADR.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, enum.Enum):
    BLOCKER = "BLOCKER"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class Status(str, enum.Enum):
    """PASS and FAIL are definitive. WARN ran but cannot fully assert.
    SKIP did not run by design. ERROR failed to run and is surfaced
    separately so it never silently counts as a pass."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"
    ERROR = "ERROR"


class Verdict(str, enum.Enum):
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    READY_WITH_NOTES = "READY_WITH_NOTES"
    READY = "READY"


class CheckFamily(str, enum.Enum):
    STATIC = "static"
    METADATA = "metadata"
    ASSERTIONS = "assertions"
    REGRESSION = "regression"
    PERFORMANCE = "performance"
    TABLEAU_STATIC = "tableau_static"
    TABLEAU_LIVE = "tableau_live"


class ExecutionType(str, enum.Enum):
    """How a check does its work. STATIC parses only, METADATA reads
    INFORMATION_SCHEMA, EXECUTION runs read-only SQL against the target."""

    STATIC = "static"
    METADATA = "metadata"
    EXECUTION = "execution"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)


class CheckResult(BaseModel):
    """The result of one check. AI assist may fill ai_explanation on an
    already-decided result; nothing may ever derive status from it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    family: CheckFamily
    severity: Severity
    status: Status
    observed: str | None = None
    expected: str | None = None
    evidence: Evidence = Field(default_factory=Evidence)
    remediation: str | None = None
    ai_explanation: str | None = None
    duration_ms: int | None = None


class SkippedFamily(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: CheckFamily
    reason: str


class Coverage(BaseModel):
    """The honesty mechanism. families_skipped is ranked so the most
    important unchecked risk is first."""

    model_config = ConfigDict(extra="forbid")

    families_run: list[CheckFamily] = Field(default_factory=list)
    families_skipped: list[SkippedFamily] = Field(default_factory=list)


class Summary(BaseModel):
    """Counts per the spec contract. The severity buckets count FAILED
    checks only. warned, errored, and skipped are additive extensions so
    ERROR is surfaced separately and never folded into passed."""

    model_config = ConfigDict(extra="forbid")

    blocker: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0
    passed: int = 0
    warned: int = 0
    errored: int = 0
    skipped: int = 0
    total: int = 0


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sql", "tableau"]
    name: str
    source_ref: str | None = None


class Environment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse: str | None = None
    role: str | None = None
    query_tag: str | None = None


class RunResult(BaseModel):
    """The machine-readable contract every surface consumes: report writers,
    the CLI exit code mapping, the web UI, and the audit record."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    timestamp: datetime
    target: Target
    ruleset_version: str
    profile: str | None = None
    verdict: Verdict
    coverage: Coverage
    summary: Summary
    checks: list[CheckResult] = Field(default_factory=list)
    environment: Environment = Field(default_factory=Environment)


def utc_now() -> datetime:
    """Timestamps in run results are always timezone-aware UTC."""
    return datetime.now(timezone.utc)

"""RunResult must accept and reproduce the exact JSON contract from
PLUMB_SPEC.md. Report writers and the web UI depend on this shape."""

import json

from plumb.engine.models import (
    CheckFamily,
    CheckResult,
    RunResult,
    Severity,
    Status,
    Verdict,
    utc_now,
)

SPEC_EXAMPLE = {
    "run_id": "0c9aa207-8de5-4d76-9a9f-bd4c9e4e4a59",
    "timestamp": "2026-06-07T12:00:00+00:00",
    "target": {
        "type": "sql",
        "name": "rpt_daily_sales",
        "source_ref": "queries/rpt_daily_sales.sql",
    },
    "ruleset_version": "2026.06.0",
    "profile": "finance",
    "verdict": "BLOCKED",
    "coverage": {
        "families_run": ["static", "metadata", "assertions", "performance"],
        "families_skipped": [{"family": "regression", "reason": "no baseline found"}],
    },
    "summary": {
        "blocker": 1,
        "high": 0,
        "medium": 2,
        "low": 3,
        "info": 4,
        "passed": 19,
        "warned": 0,
        "errored": 0,
        "skipped": 0,
        "total": 29,
    },
    "checks": [
        {
            "id": "D-GRAIN-001",
            "name": "Grain uniqueness on declared key",
            "family": "assertions",
            "severity": "BLOCKER",
            "status": "FAIL",
            "observed": "12 duplicate key groups, max duplication 4x",
            "expected": "0 duplicates on [order_id]",
            "evidence": {"query": "SELECT 1", "sample_rows": []},
            "remediation": "Aggregate to grain or correct the join key.",
            "ai_explanation": None,
            "duration_ms": None,
        }
    ],
    "environment": {
        "warehouse": "PLUMB_WH",
        "role": "PLUMB_QC_ROLE",
        "query_tag": "plumb_qc:0c9aa207-8de5-4d76-9a9f-bd4c9e4e4a59",
    },
}


def test_spec_example_validates_and_round_trips() -> None:
    result = RunResult.model_validate(SPEC_EXAMPLE)
    assert result.verdict is Verdict.BLOCKED
    assert result.coverage.families_skipped[0].family is CheckFamily.REGRESSION
    assert result.checks[0].severity is Severity.BLOCKER
    assert result.checks[0].status is Status.FAIL

    dumped = json.loads(result.model_dump_json())
    # pydantic canonicalizes UTC to the Z suffix; both are valid ISO8601
    expected = {**SPEC_EXAMPLE, "timestamp": "2026-06-07T12:00:00Z"}
    assert dumped == expected


def test_required_contract_keys_present_in_serialized_output() -> None:
    result = RunResult.model_validate(SPEC_EXAMPLE)
    dumped = result.model_dump(mode="json")
    for key in (
        "run_id",
        "timestamp",
        "target",
        "ruleset_version",
        "profile",
        "verdict",
        "coverage",
        "summary",
        "checks",
        "environment",
    ):
        assert key in dumped
    for key in ("blocker", "high", "medium", "low", "info", "passed", "total"):
        assert key in dumped["summary"]


def test_timestamp_serializes_as_iso8601_utc() -> None:
    result = RunResult.model_validate({**SPEC_EXAMPLE, "timestamp": utc_now()})
    dumped = result.model_dump(mode="json")
    assert "T" in dumped["timestamp"]
    assert dumped["timestamp"].endswith(("Z", "+00:00"))


def test_extra_fields_are_rejected() -> None:
    import pytest

    with pytest.raises(Exception, match="confidence_score"):
        RunResult.model_validate({**SPEC_EXAMPLE, "confidence_score": 0.97})


def test_ai_explanation_is_data_only_never_status() -> None:
    """The contract gives AI assist exactly one field to write. Setting it
    must not affect anything the verdict reads."""
    check = CheckResult.model_validate(SPEC_EXAMPLE["checks"][0])
    explained = check.model_copy(update={"ai_explanation": "the join fans out"})
    assert explained.status is check.status
    assert explained.severity is check.severity

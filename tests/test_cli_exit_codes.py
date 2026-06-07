"""The exit code mapping is the CI gate contract. Locked at Gate 0."""

import pytest

from plumb.cli import (
    EXIT_BLOCKED,
    EXIT_PASSING,
    EXIT_REVIEW,
    exit_code_for_verdict,
)
from plumb.engine.models import Verdict


@pytest.mark.parametrize("fail_on", ["READY_WITH_NOTES", "REVIEW", "BLOCKED"])
def test_blocked_is_always_exit_2(fail_on: str) -> None:
    assert exit_code_for_verdict(Verdict.BLOCKED, fail_on) == EXIT_BLOCKED


@pytest.mark.parametrize("fail_on", ["READY_WITH_NOTES", "REVIEW", "BLOCKED"])
def test_ready_always_passes(fail_on: str) -> None:
    assert exit_code_for_verdict(Verdict.READY, fail_on) == EXIT_PASSING


def test_review_fails_gate_at_default() -> None:
    assert exit_code_for_verdict(Verdict.REVIEW, "REVIEW") == EXIT_REVIEW


def test_review_fails_gate_at_strictest() -> None:
    assert exit_code_for_verdict(Verdict.REVIEW, "READY_WITH_NOTES") == EXIT_REVIEW


def test_review_passes_when_gate_is_blocked_only() -> None:
    """The spec makes it configurable whether REVIEW fails CI."""
    assert exit_code_for_verdict(Verdict.REVIEW, "BLOCKED") == EXIT_PASSING


def test_ready_with_notes_fails_only_strictest_gate() -> None:
    assert exit_code_for_verdict(Verdict.READY_WITH_NOTES, "READY_WITH_NOTES") == EXIT_REVIEW
    assert exit_code_for_verdict(Verdict.READY_WITH_NOTES, "REVIEW") == EXIT_PASSING
    assert exit_code_for_verdict(Verdict.READY_WITH_NOTES, "BLOCKED") == EXIT_PASSING

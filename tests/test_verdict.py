"""Contract tests for the four-tier verdict and the coverage model.

Written before the implementation. These tests are the authoritative
statement of how Plumb decides BLOCKED, REVIEW, READY_WITH_NOTES, READY,
how ERROR and WARN are treated, and how coverage gaps are ranked.
"""

from plumb.engine.models import (
    CheckFamily,
    CheckResult,
    Severity,
    Status,
    Verdict,
)
from plumb.engine.verdict import (
    FAMILY_RISK_ORDER,
    compute_coverage,
    compute_summary,
    compute_verdict,
)


def make_check(
    check_id: str = "D-GRAIN-001",
    severity: Severity = Severity.MEDIUM,
    status: Status = Status.PASS,
    family: CheckFamily = CheckFamily.ASSERTIONS,
    observed: str | None = None,
) -> CheckResult:
    return CheckResult(
        id=check_id,
        name=f"test check {check_id}",
        family=family,
        severity=severity,
        status=status,
        observed=observed,
        expected=None,
    )


class TestVerdictTiers:
    def test_all_pass_is_ready(self) -> None:
        checks = [
            make_check("A-1", Severity.BLOCKER, Status.PASS),
            make_check("A-2", Severity.HIGH, Status.PASS),
            make_check("A-3", Severity.LOW, Status.PASS),
        ]
        assert compute_verdict(checks) is Verdict.READY

    def test_blocker_fail_is_blocked(self) -> None:
        checks = [
            make_check("A-1", Severity.BLOCKER, Status.FAIL),
            make_check("A-2", Severity.HIGH, Status.PASS),
        ]
        assert compute_verdict(checks) is Verdict.BLOCKED

    def test_blocker_fail_takes_precedence_over_high_fail(self) -> None:
        checks = [
            make_check("A-1", Severity.HIGH, Status.FAIL),
            make_check("A-2", Severity.BLOCKER, Status.FAIL),
            make_check("A-3", Severity.MEDIUM, Status.FAIL),
        ]
        assert compute_verdict(checks) is Verdict.BLOCKED

    def test_high_fail_without_blocker_is_review(self) -> None:
        checks = [
            make_check("A-1", Severity.HIGH, Status.FAIL),
            make_check("A-2", Severity.BLOCKER, Status.PASS),
        ]
        assert compute_verdict(checks) is Verdict.REVIEW

    def test_medium_fail_only_is_ready_with_notes(self) -> None:
        checks = [
            make_check("A-1", Severity.MEDIUM, Status.FAIL),
            make_check("A-2", Severity.BLOCKER, Status.PASS),
        ]
        assert compute_verdict(checks) is Verdict.READY_WITH_NOTES

    def test_low_fail_only_is_ready_with_notes(self) -> None:
        checks = [make_check("A-1", Severity.LOW, Status.FAIL)]
        assert compute_verdict(checks) is Verdict.READY_WITH_NOTES

    def test_info_fail_is_still_a_note_not_unqualified_ready(self) -> None:
        checks = [make_check("A-1", Severity.INFO, Status.FAIL)]
        assert compute_verdict(checks) is Verdict.READY_WITH_NOTES

    def test_empty_run_is_ready(self) -> None:
        assert compute_verdict([]) is Verdict.READY

    def test_skip_only_run_is_ready_coverage_carries_the_honesty(self) -> None:
        checks = [make_check("A-1", Severity.BLOCKER, Status.SKIP)]
        assert compute_verdict(checks) is Verdict.READY


class TestWarnTreatment:
    def test_warn_prevents_unqualified_ready(self) -> None:
        checks = [
            make_check("A-1", Severity.LOW, Status.WARN),
            make_check("A-2", Severity.HIGH, Status.PASS),
        ]
        assert compute_verdict(checks) is Verdict.READY_WITH_NOTES

    def test_high_severity_warn_never_escalates_to_review(self) -> None:
        checks = [make_check("A-1", Severity.HIGH, Status.WARN)]
        assert compute_verdict(checks) is Verdict.READY_WITH_NOTES

    def test_blocker_severity_warn_never_escalates_to_blocked(self) -> None:
        checks = [make_check("A-1", Severity.BLOCKER, Status.WARN)]
        assert compute_verdict(checks) is Verdict.READY_WITH_NOTES


class TestErrorTreatment:
    """ERROR means the check itself failed to run. It never counts as a pass.

    An ERROR on a BLOCKER or HIGH severity check means the run cannot honestly
    claim the high-stakes thing was verified, so the verdict is capped at REVIEW.
    An ERROR on a lower severity check is a note. See ADR-0001.
    """

    def test_error_on_blocker_check_forces_review(self) -> None:
        checks = [
            make_check("A-1", Severity.BLOCKER, Status.ERROR),
            make_check("A-2", Severity.HIGH, Status.PASS),
        ]
        assert compute_verdict(checks) is Verdict.REVIEW

    def test_error_on_high_check_forces_review(self) -> None:
        checks = [make_check("A-1", Severity.HIGH, Status.ERROR)]
        assert compute_verdict(checks) is Verdict.REVIEW

    def test_error_on_medium_check_is_a_note(self) -> None:
        checks = [make_check("A-1", Severity.MEDIUM, Status.ERROR)]
        assert compute_verdict(checks) is Verdict.READY_WITH_NOTES

    def test_error_on_low_check_is_a_note(self) -> None:
        checks = [make_check("A-1", Severity.LOW, Status.ERROR)]
        assert compute_verdict(checks) is Verdict.READY_WITH_NOTES

    def test_error_never_counts_as_pass(self) -> None:
        checks = [make_check("A-1", Severity.INFO, Status.ERROR)]
        assert compute_verdict(checks) is not Verdict.READY

    def test_blocker_fail_beats_blocker_error(self) -> None:
        checks = [
            make_check("A-1", Severity.BLOCKER, Status.ERROR),
            make_check("A-2", Severity.BLOCKER, Status.FAIL),
        ]
        assert compute_verdict(checks) is Verdict.BLOCKED


class TestSummary:
    def test_summary_matches_spec_contract_example(self) -> None:
        """Reproduce the spec's example: 1 blocker, 2 medium, 3 low, 4 info
        failed, 19 passed, 29 total."""
        checks = (
            [make_check(f"B-{i}", Severity.BLOCKER, Status.FAIL) for i in range(1)]
            + [make_check(f"M-{i}", Severity.MEDIUM, Status.FAIL) for i in range(2)]
            + [make_check(f"L-{i}", Severity.LOW, Status.FAIL) for i in range(3)]
            + [make_check(f"I-{i}", Severity.INFO, Status.FAIL) for i in range(4)]
            + [make_check(f"P-{i}", Severity.MEDIUM, Status.PASS) for i in range(19)]
        )
        summary = compute_summary(checks)
        assert summary.blocker == 1
        assert summary.high == 0
        assert summary.medium == 2
        assert summary.low == 3
        assert summary.info == 4
        assert summary.passed == 19
        assert summary.total == 29
        assert compute_verdict(checks) is Verdict.BLOCKED

    def test_severity_buckets_count_failures_only(self) -> None:
        checks = [
            make_check("A-1", Severity.BLOCKER, Status.PASS),
            make_check("A-2", Severity.HIGH, Status.WARN),
            make_check("A-3", Severity.HIGH, Status.ERROR),
            make_check("A-4", Severity.MEDIUM, Status.SKIP),
        ]
        summary = compute_summary(checks)
        assert summary.blocker == 0
        assert summary.high == 0
        assert summary.medium == 0
        assert summary.passed == 1
        assert summary.warned == 1
        assert summary.errored == 1
        assert summary.skipped == 1
        assert summary.total == 4

    def test_error_is_surfaced_separately_never_as_pass(self) -> None:
        checks = [make_check("A-1", Severity.HIGH, Status.ERROR)]
        summary = compute_summary(checks)
        assert summary.passed == 0
        assert summary.errored == 1


class TestCoverage:
    def test_families_run_are_listed(self) -> None:
        checks = [
            make_check("S-1", family=CheckFamily.STATIC),
            make_check("D-1", family=CheckFamily.ASSERTIONS),
        ]
        coverage = compute_coverage(checks)
        assert CheckFamily.STATIC in coverage.families_run
        assert CheckFamily.ASSERTIONS in coverage.families_run
        assert coverage.families_skipped == []

    def test_declared_skip_is_reported_with_reason(self) -> None:
        checks = [make_check("S-1", family=CheckFamily.STATIC)]
        coverage = compute_coverage(
            checks, declared_skips={CheckFamily.REGRESSION: "no baseline found"}
        )
        assert coverage.families_run == [CheckFamily.STATIC]
        assert len(coverage.families_skipped) == 1
        skipped = coverage.families_skipped[0]
        assert skipped.family is CheckFamily.REGRESSION
        assert skipped.reason == "no baseline found"

    def test_family_with_all_results_skipped_counts_as_skipped(self) -> None:
        checks = [
            make_check("S-1", family=CheckFamily.STATIC, status=Status.PASS),
            make_check(
                "D-1",
                family=CheckFamily.ASSERTIONS,
                status=Status.SKIP,
                observed="no key declared",
            ),
        ]
        coverage = compute_coverage(checks)
        assert coverage.families_run == [CheckFamily.STATIC]
        assert len(coverage.families_skipped) == 1
        assert coverage.families_skipped[0].family is CheckFamily.ASSERTIONS
        assert coverage.families_skipped[0].reason == "no key declared"

    def test_family_with_one_executed_check_counts_as_run(self) -> None:
        checks = [
            make_check("D-1", family=CheckFamily.ASSERTIONS, status=Status.SKIP),
            make_check("D-2", family=CheckFamily.ASSERTIONS, status=Status.PASS),
        ]
        coverage = compute_coverage(checks)
        assert coverage.families_run == [CheckFamily.ASSERTIONS]
        assert coverage.families_skipped == []

    def test_skipped_families_are_ranked_by_risk(self) -> None:
        """The analyst must see the most important unchecked risk first.
        Assertions outrank regression, which outranks metadata, static,
        performance. See ADR-0002."""
        coverage = compute_coverage(
            [],
            declared_skips={
                CheckFamily.PERFORMANCE: "profile disabled performance checks",
                CheckFamily.ASSERTIONS: "no checks configured",
                CheckFamily.REGRESSION: "no baseline found",
            },
        )
        ranked = [s.family for s in coverage.families_skipped]
        assert ranked == [
            CheckFamily.ASSERTIONS,
            CheckFamily.REGRESSION,
            CheckFamily.PERFORMANCE,
        ]

    def test_families_run_are_ordered_by_risk(self) -> None:
        checks = [
            make_check("P-1", family=CheckFamily.PERFORMANCE),
            make_check("S-1", family=CheckFamily.STATIC),
            make_check("D-1", family=CheckFamily.ASSERTIONS),
        ]
        coverage = compute_coverage(checks)
        assert coverage.families_run == [
            CheckFamily.ASSERTIONS,
            CheckFamily.STATIC,
            CheckFamily.PERFORMANCE,
        ]

    def test_risk_order_covers_every_family(self) -> None:
        assert set(FAMILY_RISK_ORDER) == set(CheckFamily)

    def test_clean_run_with_skips_is_ready_with_visible_gaps(self) -> None:
        """The Phase 1 acceptance shape: READY verdict, but coverage lists the
        skipped reconciliation and missing baseline explicitly and ranked."""
        checks = [
            make_check("S-1", family=CheckFamily.STATIC, status=Status.PASS),
            make_check("M-1", family=CheckFamily.METADATA, status=Status.PASS),
        ]
        verdict = compute_verdict(checks)
        coverage = compute_coverage(
            checks,
            declared_skips={
                CheckFamily.REGRESSION: "no baseline found",
                CheckFamily.ASSERTIONS: "reconciliation not configured",
            },
        )
        assert verdict is Verdict.READY
        assert [s.family for s in coverage.families_skipped] == [
            CheckFamily.ASSERTIONS,
            CheckFamily.REGRESSION,
        ]

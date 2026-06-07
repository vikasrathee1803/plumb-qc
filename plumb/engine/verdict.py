"""The four-tier verdict and the coverage model.

This is the only place verdict logic lives. No surface (CLI, web UI, report
writer) may reimplement it. The rules, from PLUMB_SPEC.md:

- Any BLOCKER fail: BLOCKED.
- No blocker, any HIGH fail: REVIEW.
- Only MEDIUM, LOW, or INFO issues: READY_WITH_NOTES.
- Nothing fails: READY.

Treatment of non-definitive statuses (ADR-0001):
- WARN ran but could not fully assert. Any WARN is a note. It prevents an
  unqualified READY and never escalates further.
- ERROR means the check failed to run. It never counts as a pass. An ERROR
  on a BLOCKER or HIGH severity check caps the verdict at REVIEW, because
  the run cannot honestly claim the high-stakes thing was verified.
- SKIP is a coverage concern, not a verdict concern.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from plumb.engine.models import (
    CheckFamily,
    CheckResult,
    Coverage,
    Severity,
    SkippedFamily,
    Status,
    Summary,
    Verdict,
)

# Ranking for coverage gaps, most important unchecked risk first (ADR-0002).
# Assertions catch the failure modes Plumb exists for (fan-out, recon drift),
# regression is the confidence centerpiece, metadata catches existence and
# type errors, static is preventive, performance is advisory.
FAMILY_RISK_ORDER: tuple[CheckFamily, ...] = (
    CheckFamily.ASSERTIONS,
    CheckFamily.REGRESSION,
    CheckFamily.METADATA,
    CheckFamily.STATIC,
    CheckFamily.PERFORMANCE,
    CheckFamily.TABLEAU_STATIC,
    CheckFamily.TABLEAU_LIVE,
)

_RISK_RANK: dict[CheckFamily, int] = {f: i for i, f in enumerate(FAMILY_RISK_ORDER)}

_SKIPPED_FAMILY_FALLBACK_REASON = "all checks in family were skipped"


def compute_verdict(checks: Iterable[CheckResult]) -> Verdict:
    """Deterministic verdict from check results. Nothing else may set it."""
    fail_severities: set[Severity] = set()
    error_severities: set[Severity] = set()
    has_warn = False

    for check in checks:
        if check.status is Status.FAIL:
            fail_severities.add(check.severity)
        elif check.status is Status.ERROR:
            error_severities.add(check.severity)
        elif check.status is Status.WARN:
            has_warn = True

    if Severity.BLOCKER in fail_severities:
        return Verdict.BLOCKED
    if Severity.HIGH in fail_severities:
        return Verdict.REVIEW
    if error_severities & {Severity.BLOCKER, Severity.HIGH}:
        return Verdict.REVIEW
    if fail_severities or error_severities or has_warn:
        return Verdict.READY_WITH_NOTES
    return Verdict.READY


def compute_summary(checks: Iterable[CheckResult]) -> Summary:
    """Counts per the contract. Severity buckets count failures only.
    ERROR is counted separately and never folded into passed."""
    summary = Summary()
    fail_bucket: dict[Severity, int] = dict.fromkeys(Severity, 0)

    for check in checks:
        summary.total += 1
        if check.status is Status.FAIL:
            fail_bucket[check.severity] += 1
        elif check.status is Status.PASS:
            summary.passed += 1
        elif check.status is Status.WARN:
            summary.warned += 1
        elif check.status is Status.ERROR:
            summary.errored += 1
        elif check.status is Status.SKIP:
            summary.skipped += 1

    summary.blocker = fail_bucket[Severity.BLOCKER]
    summary.high = fail_bucket[Severity.HIGH]
    summary.medium = fail_bucket[Severity.MEDIUM]
    summary.low = fail_bucket[Severity.LOW]
    summary.info = fail_bucket[Severity.INFO]
    return summary


def compute_coverage(
    checks: Iterable[CheckResult],
    declared_skips: Mapping[CheckFamily, str] | None = None,
) -> Coverage:
    """Build the coverage block. A family ran if at least one of its checks
    produced a non-SKIP result. A family is skipped if the runner declared
    it skipped (for example regression with no baseline) or if every one of
    its results was a SKIP. Skipped families are ranked by risk so the
    analyst sees the most important unchecked risk first."""
    results_by_family: dict[CheckFamily, list[CheckResult]] = {}
    for check in checks:
        results_by_family.setdefault(check.family, []).append(check)

    families_run: list[CheckFamily] = []
    skipped: dict[CheckFamily, str] = dict(declared_skips or {})

    for family, results in results_by_family.items():
        if any(r.status is not Status.SKIP for r in results):
            families_run.append(family)
            skipped.pop(family, None)
        elif family not in skipped:
            reason = next(
                (r.observed for r in results if r.observed),
                _SKIPPED_FAMILY_FALLBACK_REASON,
            )
            skipped[family] = reason or _SKIPPED_FAMILY_FALLBACK_REASON

    families_run.sort(key=lambda f: _RISK_RANK[f])
    families_skipped = [
        SkippedFamily(family=family, reason=reason)
        for family, reason in sorted(skipped.items(), key=lambda kv: _RISK_RANK[kv[0]])
    ]
    return Coverage(families_run=families_run, families_skipped=families_skipped)

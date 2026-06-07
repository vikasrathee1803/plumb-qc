# ADR-0001: ERROR and WARN treatment in the verdict

Date: 2026-06-07. Status: accepted.

The spec defines the four-tier verdict over FAIL statuses and says ERROR
never counts as a pass, but does not say how ERROR and WARN move the
verdict. Decision:

- WARN (ran, could not fully assert) is a note. Any WARN prevents an
  unqualified READY and never escalates past READY_WITH_NOTES. Escalation
  requires a definitive FAIL.
- ERROR (check failed to run) on a BLOCKER or HIGH severity check caps
  the verdict at REVIEW: the run cannot honestly claim the high-stakes
  assertion was verified. ERROR on MEDIUM, LOW, or INFO is a note.
- ERROR counts are reported separately in the summary (errored field) and
  are never folded into passed.

Reversibility: cheap. The logic lives only in plumb/engine/verdict.py and
is fully covered by tests/test_verdict.py.

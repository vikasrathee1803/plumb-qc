# ADR-0005: Exit code mapping under the fail_on gate

Date: 2026-06-07. Status: accepted.

The spec fixes exit codes (0 passing, 1 REVIEW, 2 BLOCKED, 3 tool error)
and separately makes the CI gate configurable via fail_on. The
interaction was open. Decision, implemented in plumb.cli.exit_code_for_verdict:

- BLOCKED is always exit 2.
- A verdict at or below the fail_on gate (rank order BLOCKED < REVIEW <
  READY_WITH_NOTES < READY) exits 1.
- Otherwise exit 0. So with fail_on BLOCKED, a REVIEW verdict exits 0,
  which is the spec's "configurable whether REVIEW fails CI".
- Exit 3 is reserved for tool, config, and connection errors and is never
  produced by a verdict.

Reversibility: cheap. One pure function, fully covered by
tests/test_cli_exit_codes.py.

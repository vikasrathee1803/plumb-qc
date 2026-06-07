# SPRINT

Updated: 2026-06-07. Phase: 0 complete, awaiting Gate 0 approval.

## Now

- GATE 0 review: contracts, registry seam, read-only guard, verdict tests.
- Blocked on: explicit approval to fan out Phase 1 streams.

## Phase 0 burndown (all done)

- [x] Repo scaffold: pyproject (pinned), folder tree per spec, Dockerfile,
      .gitignore, README
- [x] tests/test_verdict.py written first; all four tiers plus coverage
- [x] engine/models.py: CheckResult, RunResult per JSON contract
- [x] engine/verdict.py: verdict, summary, coverage with risk ranking
- [x] engine/registry.py: plugin seam plus tests
- [x] config/models.py and loader.py: Ruleset, CheckSpec, Profile,
      ConnectionProfile, profile merge, version pinning, loud failure
- [x] connect/snowflake.py: three auth paths, query tag, timeout, row cap,
      read-only guard plus the proving test
- [x] cli.py skeleton with the exit code contract locked and tested
- [x] rules/plumb.yml plus finance and marketing profiles, all validated
      by tests
- [x] docs: ARCHITECTURE, PRD, BACKLOG, TEST-PLAN, RUNBOOK, ADR-0001..0007

## Phase 1 parallelization map (ready to spawn on Gate 0 approval)

Streams run against the locked contracts and do not block each other:

- Stream A: checks/sql_static.py, checks/sql_meta.py (S-LINT, S-STAT-001
  to 010, S-META-001 to 004)
- Stream B: checks/sql_assertions.py (D-GRAIN, D-NULL, D-RI, D-DOMAIN,
  D-RANGE, D-FRESH, D-RECON, D-DUP, D-ADD) plus the PII redaction pipeline
- Stream C: baseline/store.py then checks/sql_regression.py (internally
  ordered, externally parallel)
- Stream D: checks/sql_performance.py (P-PROF, P-COST, P-SPILL, P-CARD)
- Stream E: report/html.py, json_out.py, junit.py (depends only on
  RunResult, starts immediately)

Then serial integration: engine/runner.py (including the audit JSONL
record), full cli.py surface, QUERY_HISTORY verification, docs. Then the
spec-mandated hard stop at Gate 1.

## Risks

- PLUMB_QC_ROLE and PLUMB_WH provisioning is the one external dependency.
  Needed before Gate 1 acceptance can run against a live account. Grants
  documented in RUNBOOK.md. Owner: user's Snowflake admin.
- sqlglot parses EXPLAIN as Command in the snowflake dialect; the guard
  unwraps it textually. Revisit if sqlglot adds native support.

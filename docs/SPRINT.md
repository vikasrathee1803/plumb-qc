# SPRINT

Updated: 2026-06-07. Phase: 1 complete; Phase 2 in progress (Tableau static
done, live Snowflake demonstrated).

## Now

- Phase 2 underway at the user's direction (live Snowflake + Tableau).
- Done this round: Tableau static catalog (T-*), live engine run against
  real Snowflake data via the MCP connection, two live-found bugs fixed
  (Click help crash, full_dup_query grouping), OAuth profile scaffolded.
- 227 tests pass, ruff clean, mypy clean, no em dashes.
- Blocked on the user for: account/user/role/warehouse + OAuth token to
  complete the fully-native live run; decisions on web UI and AI assist.

## Live verification (real data, PORTFOLIO_DEMO_DB.ANALYTICS)

- Plumb's generated SQL runs clean on live Snowflake (grain, null, dup,
  recon, freshness queries validated against V_CUSTOMER_LTV, 99,996 rows).
- Real verdict: BLOCKED. Grain/null/dup PASS; freshness FAIL (1990s data);
  recon FAIL (two views off by 907B). All true findings.
- AC6 (query tag, PLUMB_WH, timeout, row cap) still needs the native
  connector against a provisioned warehouse to confirm in QUERY_HISTORY;
  the MCP transport does not set those session params.

## Phase 2 status: complete

- [x] Tableau static analysis (T-* catalog)
- [x] Shared baseline store (configured path / mounted object store, ADR-0012)
- [x] AI assist (opt-in): explain / fix / recon SQL, never sets a status
- [x] Web UI: FastAPI wrapping the engine + Vite/React SPA, one command

Live native Snowflake confirmed (AC6 verified in QUERY_HISTORY). 260 tests
pass, ruff + mypy clean, no em dashes. Web UI verified live over HTTP:
SQL check BLOCKED, Tableau upload REVIEW, self-contained HTML report.

## Phase 1 burndown (all done)

- [x] Stream A: sql_static (S-LINT, S-STAT-001..010), sql_meta (S-META-001..004)
- [x] Stream B: sql_assertions (D-GRAIN/NULL/RI/DOMAIN/RANGE/FRESH/RECON/DUP/ADD)
- [x] Stream C: baseline/store.py (Parquet + Protocol seam), sql_regression (R-DIFF, R-AGG)
- [x] Stream D: sql_performance (P-PROF/COST/SPILL/CARD)
- [x] Stream E: report writers (HTML self-contained, JSON, JUnit)
- [x] Integration: engine/runner.py, audit JSONL, full CLI surface
- [x] Coverage check-level gaps (ADR-0009), evidence redaction pipeline
- [x] One-plus fixture test per family; acceptance suite by criterion ID

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

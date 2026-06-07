# TEST PLAN

QA owns this file and has veto on done. Run: `.venv\Scripts\python -m pytest`

## Phase 0 coverage (current: 96 tests, all passing)

| Area | File | What is proven |
|---|---|---|
| Verdict tiers | tests/test_verdict.py | all four tiers, precedence, WARN and ERROR treatment per ADR-0001, summary matches the spec example, coverage ranking per ADR-0002 |
| Contracts | tests/test_models_contract.py | the spec JSON example validates and round-trips; extras rejected; ai_explanation cannot move a status |
| Registry | tests/test_registry.py | registration, duplicate refusal, unknown id error, family filter |
| Config | tests/test_config_loader.py | valid load, four malformed-ruleset failure modes, pinning, profile merge semantics, connection profile auth rules, password refusal |
| Shipped rules | tests/test_shipped_rules.py | rules/plumb.yml and both profiles always validate and resolve |
| Read-only guard | tests/test_readonly_guard.py | 29 non-read statements refused (writes, DDL, DML, transactions, session and account commands, multi-statement, unparseable, EXPLAIN-of-write), 11 reads allowed |
| Session | tests/test_connect_session.py | query tag, timeout, warehouse, role on every connection; row cap with truncation flag; guard runs before any cursor activity; auth kwargs per path; no password key ever; secrets only from env or keyring |
| Exit codes | tests/test_cli_exit_codes.py | full verdict x fail_on matrix per ADR-0005 |

## Phase 2 coverage (added)

| Area | File | What is proven |
|---|---|---|
| Tableau static | tests/test_checks_tableau.py | lxml parser plus the T-* catalog against a fixture .twb; .twbx zip read; target-type isolation |
| Shared baseline | tests/test_baseline_store.py | local and shared stores satisfy the Protocol; factory; shared index; a teammate store at the same path reproduces the diff |
| AI assist | tests/test_ai_assist.py | tolerant JSON parser; explain attaches only ai_explanation; verdict and all statuses identical with and without; graceful degradation on parse/exception; fix and recon contracts |
| Web backend | tests/test_web_api.py | every endpoint returns the RunResult contract via the same engine; SQL, Tableau upload, profiles, report HTML, SPA served |
| Phase 2 acceptance | tests/test_acceptance_phase2.py | P2-AC1..AC4 by ID, including web-equals-CLI verdict equivalence |
| SQL builder shapes | tests/test_sql_builders.py | generated SQL passes the read-only guard; full-dup grouping; no force-quoting; safe literals |

SPA build (not a pytest target): `cd web/ui && npm install && npm run build`.

## Phase 1 additions (planned)

- One fixture-backed test per check family minimum (spec mandate);
  fixture corpus under tests/fixtures/ with a known-bad query per failure
  mode: fan-out join, NOT IN null trap, cartesian join, stale freshness,
  recon drift, baseline diff.
- Runner integration test: ruleset in, RunResult out, deterministic.
- Report writer golden-file tests against a fixed RunResult.
- CLI end-to-end with a mocked session: exit codes per verdict.
- Live verification checklist (needs PLUMB_WH): QUERY_HISTORY shows tag,
  warehouse, timeout; row cap observed.

## Standing rules

- Verdict logic changes require a test in the same commit.
- No check merges without a fixture test.
- ERROR paths are tested, not assumed.

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

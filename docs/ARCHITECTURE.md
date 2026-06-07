# Plumb Architecture

Authoritative spec: ../PLUMB_SPEC.md. This document records how the
implementation realizes it and which seams are load-bearing. Decisions
the spec left open are in docs/adr/.

## The contracts (locked at Gate 0)

| Contract | Lives in | Consumed by |
|---|---|---|
| CheckResult, RunResult, Severity, Status, Verdict, CheckFamily, Coverage, Summary | plumb/engine/models.py | every check, every writer, CLI, web UI, AI assist |
| Verdict and coverage logic | plumb/engine/verdict.py | engine runner only; no surface reimplements it |
| Check registry seam | plumb/engine/registry.py | checks register themselves; runner discovers |
| Ruleset, CheckSpec, Profile, ConnectionProfile | plumb/config/models.py | loader, runner, CLI |
| Read-only session | plumb/connect/snowflake.py | every metadata and execution check |
| Exit code mapping | plumb/cli.py exit_code_for_verdict | CI gate |

Changing any of these is a breaking change and needs an ADR plus a Gate
review.

## Invariant enforcement map

- Deterministic verdicts: verdict.py is pure; CheckResult.ai_explanation
  is the only field AI may write and nothing in verdict.py reads it
  (tested in test_models_contract.py).
- Read-only: assert_read_only runs inside SnowflakeSession.execute before
  any cursor exists; policy in ADR-0003; proven by test_readonly_guard.py.
- Tag, warehouse, timeout, row cap: assembled in build_connect_kwargs so
  no session can exist without them; row cap applied at fetch; tested in
  test_connect_session.py. QUERY_HISTORY verification is a Phase 1 gate
  item (needs a live account).
- Secrets: ConnectionProfile rejects any password field by name; secrets
  come from keyring or environment only (ADR-0004).
- Loud config failure: every model forbids unknown fields; loader wraps
  pydantic errors into readable ConfigError; CLI maps to exit 3.
- PII redaction and evidence caps: ruleset defaults (evidence_sample_rows,
  redact_pii, aggregate_only) are modeled now; the redaction pipeline
  lands with the assertions family in Phase 1.

## Scalability seams

- New check: drop a function in plumb/checks/ decorated with
  register_check. Engine, verdict, and writers do not change.
- New team: add a profile YAML that overlays the base ruleset
  (resolve_profile merge semantics documented in config/models.py).
- New output format: new writer consuming RunResult.
- New surface (web UI, AI assist): wraps engine.runner and RunResult,
  never reimplements verdict logic.
- Shared baselines (Phase 2): baseline/store.py will define a store
  interface; local Parquet plus manifest is the default implementation.
- Stateless runs: no shared mutable state between runs; CI can fan out.

## Module status

| Module | State |
|---|---|
| engine/models.py, verdict.py, registry.py | built, tested |
| config/models.py, loader.py | built, tested |
| connect/snowflake.py | built, tested offline; live QUERY_HISTORY check pending account access |
| cli.py | skeleton: version, rules pin, rules show; check commands land in Phase 1 |
| engine/runner.py, checks/*, baseline/store.py, report/* | Phase 1 |
| web/, ai/, tableau checks | Phase 2 |

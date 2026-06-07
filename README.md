# Plumb

A local-first, centrally governed QC and confidence engine. Plumb lets a
BI analyst prove a Snowflake SQL build or a Tableau workbook is correct
before it ships, and produces a shareable confidence report.

## What it does

- Runs deterministic checks against your SQL: static analysis, schema and
  metadata validation, data assertions (grain, nulls, referential
  integrity, freshness, reconciliation), regression diff against a saved
  baseline, and performance smells.
- Produces a tiered verdict: BLOCKED, REVIEW, READY_WITH_NOTES, or READY,
  with an honest coverage statement of what ran and what was skipped.
- Writes a self-contained HTML report, JSON, and JUnit XML for CI.

## Install

```
pipx install plumb        # from the internal package index
```

## Quick start (Phase 1 surface)

```
plumb init                                   # scaffold connection profile
plumb rules pin 2026.06.0                    # pin the team standard
plumb check sql --query daily_sales.sql --profile finance
plumb report open
```

Exit codes for CI: 0 passing, 1 REVIEW, 2 BLOCKED, 3 tool error.

## Guarantees

- Read-only everywhere. The engine refuses any statement that is not a
  read, and there is a test that proves it.
- Deterministic verdicts. No LLM ever decides a pass or fail.
- Every query is tagged plumb_qc:{run_id}, runs on the dedicated PLUMB_WH
  warehouse, and respects the statement timeout and row cap.
- Auth is key-pair, externalbrowser SSO, or OAuth. No passwords, no
  secrets in config or source.

## Web UI (Phase 2)

```
cd web/ui && npm install && npm run build      # once
plumb web                                       # serves API + SPA on :8000
```

The web UI wraps the same engine and renders the same verdict, coverage,
and report as the CLI. Run a SQL check or upload a .twb/.twbx.

## AI assist (Phase 2, opt-in)

`plumb check sql --query f.sql --explain` attaches plain-English
explanations to failing checks. It runs only after the verdict is decided
and never changes a status. Needs ANTHROPIC_API_KEY (env or OS keychain);
without it, the run is unaffected.

## Shared baselines (Phase 2)

Point all analysts at one baseline location (a network share or mounted
object store) via ~/.plumb/baselines.yml ({kind: shared, path: ...}) or
PLUMB_BASELINE_DIR. Never a Snowflake write (ADR-0012).

## Project state

Phases 0, 1, and 2 are complete: SQL engine, Tableau static analysis, web
UI, opt-in AI assist, shared baselines. Verified against live Snowflake.
Phase 3 (Tableau live reconciliation and lineage) is deferred. See
docs/SPRINT.md for status and docs/ARCHITECTURE.md for the contracts.

## Development

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check plumb tests
.venv\Scripts\python -m mypy plumb
```

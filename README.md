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

## Project state

Phase 0 (engine core contracts) is complete. Phase 1 (SQL engine end to
end) is next. See docs/SPRINT.md for live status and docs/ARCHITECTURE.md
for the contracts.

## Development

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check plumb tests
.venv\Scripts\python -m mypy plumb
```

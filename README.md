# Plumb

[![CI](https://github.com/vikasrathee1803/plumb-qc/actions/workflows/ci.yml/badge.svg)](https://github.com/vikasrathee1803/plumb-qc/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Download](https://img.shields.io/badge/download-Windows%20portable-2ea44f.svg)](https://github.com/vikasrathee1803/plumb-qc/releases/latest)

A local-first, centrally governed QC and confidence engine. Plumb lets a
BI analyst prove a Snowflake SQL build or a Tableau workbook is correct
before it ships, and produces a shareable confidence report.

![Plumb catching a cross-join fan-out before it ships](docs/screenshot.png)

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

## Run it (web UI)

No Python or Node? Download the **portable build** from the
[latest release](https://github.com/vikasrathee1803/plumb-qc/releases/latest):
unzip and double-click `run.bat`. It carries its own Python and every
dependency, runs on http://127.0.0.1:8777, and needs no install and no admin.

From a source checkout: one click, nothing to type, double-click **`run.bat`**
(Windows) or run **`./run.sh`** (macOS/Linux). It builds the UI on first run,
then opens http://127.0.0.1:8000.

Or run it by hand:

```
cd web/ui && npm install && npm run build      # once
plumb web                                       # serves API + SPA on :8000
```

The web UI wraps the same engine and renders the same verdict, coverage, and
report as the CLI. Run a SQL check, upload a .twb/.twbx, open the query map, or
configure your Snowflake/Tableau connection from the gear icon (credentials stay
local: config in ~/.plumb, secrets in your OS keychain).

Frontend hot-reload (for development): `cd web/ui && npm run dev` starts the
backend and Vite together (Vite on :5173 proxies /api to :8000). It sets
PLUMB_DISABLE_AUTH for loopback dev only; `plumb web` keeps the API token on.

## AI assist (Phase 2, opt-in)

`plumb check sql --query f.sql --explain` attaches plain-English
explanations to failing checks. It runs only after the verdict is decided
and never changes a status. AI assist runs in-database via Snowflake Cortex
(no external API key, no data egress); enable it with PLUMB_CORTEX_MODEL on a
live run. Without it, the run is unaffected.

## Migration parity (galaxy / UDM cut-over)

Migrating workbooks to a new warehouse or presentation layer? Prove the
numbers match before anyone eyeballs dashboards side by side:

```
plumb parity snapshot --workbook sales.twbx --map galaxy-map.yml   # legacy side
# ... re-point the workbook (Tableau Autopilot swap-connection) ...
plumb parity check    --workbook sales.twbx --map galaxy-map.yml   # migrated side
```

`snapshot` derives the Snowflake objects the workbook depends on, measures
them read-only (row counts, per-column aggregates, null/distinct counts,
optional grain groups), and saves one baseline per object. `check` measures
the mapped target objects and compares: drift is a BLOCKED verdict with the
worst offenders named. Joins/unions/extract-only sources are refused and
reported in coverage, never guessed at. The map file declares old→new
renames, keys, grain, and tolerances; unlisted objects compare under their
own names. See docs/RUNBOOK.md for the full migration play and
docs/adr/ADR-0013-migration-parity-family.md for the design.

## Shared baselines (Phase 2)

Point all analysts at one baseline location (a network share or mounted
object store) via ~/.plumb/baselines.yml ({kind: shared, path: ...}) or
PLUMB_BASELINE_DIR. Never a Snowflake write (ADR-0012).

## Troubleshooting

If anything will not start (for example `ModuleNotFoundError: No module named
'uvicorn'`), run the self-check first:

```
plumb doctor                    # installed CLI
python scripts/selfcheck.py     # from a source checkout
check.bat                       # inside the portable build
```

It reports every runtime dependency, every module import, the engine, and the
web app as PASS or FAIL, so a missing dependency or broken import is named
exactly instead of surfacing as a cryptic traceback at launch. In the portable
build, always start with `run.bat` (it uses the bundled Python); double-clicking
`run_plumb.py` can pick a different Python that lacks the dependencies.

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

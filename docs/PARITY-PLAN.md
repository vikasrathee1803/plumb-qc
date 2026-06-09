# Migration Parity Validator — Plan of Record

_2026-06-09 · Status: APPROVED FOR BUILD · Host: Plumb (new check family) · Branch: parity-validator_

## 1. Product statement

The team is migrating to the UDM / galaxy warehouse with a new presentation layer.
Every Tableau workbook must be re-pointed at the new layer and **proven to show the
same numbers**. Today that proof is analysts eyeballing dashboards side by side.

The Migration Parity Validator makes it one command per workbook:

```
plumb parity snapshot --workbook sales.twbx --profile legacy --map galaxy-map.yml
plumb parity check    --workbook sales.twbx --profile galaxy --map galaxy-map.yml
```

`snapshot` reads the workbook, derives the Snowflake objects it depends on, runs
read-only parity metrics (row counts, per-column aggregates, null/distinct counts,
optional grain-grouped counts) against the **legacy** side, and stores them as
baselines. `check` runs the same metrics against the **mapped galaxy objects** and
produces a Plumb verdict (READY / REVIEW / BLOCKED) with honest coverage, HTML/JSON/
JUnit reports, and CI exit codes. Workbook re-pointing itself stays in Tableau
Autopilot (`swap-connection` / `swap-source`) — this product proves the numbers.

### Goals
- Table-level parity proof per workbook, both-sides or snapshot-now/compare-later.
- Zero new privileges: read-only Snowflake (existing Plumb session guarantees),
  local workbook files. No dbt, no Airflow, no Atlan, no Tableau API.
- Production grade: Plumb's bar (deterministic verdicts, coverage honesty,
  read-only proof, typed, tested, lint/mypy clean).

### Non-goals (v1)
- Compiling Tableau calculations to SQL (v2: calc-level reconciliation).
- Editing workbooks (Autopilot's job). Scheduling (the team's CI's job).
- Join/union relation decomposition — refused and **reported in coverage**.

## 2. Architecture

### Component view

```
                        ┌─────────────────────────────────────────────┐
                        │                plumb CLI (typer)             │
                        │   parity snapshot │ parity check │ report    │
                        └──────────┬───────────────┬───────────────────┘
                                   │               │
                    ┌──────────────▼───────────────▼──────────────┐
                    │           plumb/parity/runner.py             │
                    │  orchestrates: extract → map → metrics →     │
                    │  snapshot|compare → RunResult                │
                    └──┬──────────┬──────────┬──────────┬──────────┘
                       │          │          │          │
        ┌──────────────▼─┐  ┌─────▼─────┐ ┌──▼───────┐ ┌▼──────────────┐
        │ parity/sources │  │ parity/   │ │ parity/  │ │ checks/       │
        │ .py            │  │ mapping.py│ │ metrics  │ │ parity.py     │
        │ workbook →     │  │ map.yml → │ │ .py      │ │ M-* checks    │
        │ SourceRelation │  │ ObjectMap │ │ metric   │ │ (registry)    │
        │ (lxml, reuses  │  │ old→new   │ │ SQL gen  │ └───────┬───────┘
        │ checks/_tableau│  └───────────┘ └────┬─────┘         │
        └────────────────┘                     │         ┌─────▼──────────┐
                                               │         │ engine/runner  │
                  ┌────────────────────────────▼──┐      │ verdict.py     │
                  │ connect/snowflake.py (EXISTS)  │      │ (UNCHANGED)    │
                  │ read-only · tagged · capped    │      └─────┬──────────┘
                  └───────────────┬────────────────┘            │
                                  │                   ┌─────────▼─────────┐
                  ┌───────────────▼────────────────┐  │ report/ html json │
                  │ baseline/store.py (EXISTS)     │  │ junit (UNCHANGED) │
                  │ parity snapshots = namespaced  │  └───────────────────┘
                  │ baselines (parquet+manifest)   │
                  └────────────────────────────────┘
```

### Sequence — snapshot then check (the normal migration flow)

```
 analyst        sources.py      mapping.py     metrics.py    Snowflake(legacy)  store
   │ snapshot       │               │              │               │              │
   ├───────────────►│ parse .twbx   │              │               │              │
   │                ├──relations───►│ resolve      │               │              │
   │                │               ├─(identity)──►│ build SQL     │              │
   │                │               │              ├──SELECTs─────►│              │
   │                │               │              │◄──metrics─────┤              │
   │                │               │              ├──save snapshots─────────────►│
   │ … galaxy presentation layer lands, Autopilot swaps the workbook …            │
   │ check          │               │              │          Snowflake(galaxy)   │
   ├───────────────►│ parse .twbx   │              │               │              │
   │                ├──relations───►│ map old→new ►│ build SQL     │              │
   │                │               │              ├──SELECTs─────►│              │
   │                │               │              │◄──metrics─────┤              │
   │                │   M-* checks compare vs snapshots◄───────────────────load───┤
   │◄── RunResult: verdict + coverage + HTML/JSON/JUnit ────────────────────────  │
```

### Where it plugs into existing contracts (no contract changes)

| Existing seam | How parity uses it |
|---|---|
| `CheckFamily` enum (engine/models.py:45) | new value `MIGRATION_PARITY` |
| `@register_check` (engine/registry.py:69) | M-* checks register like every other family |
| `CheckContext.extras` (engine/registry.py:27) | carries `parity_bundle` (relations, mapping, mode, snapshot prefix) |
| `engine/runner.py` `_FAMILIES_FOR_TARGET` | new target kind `parity` → `[MIGRATION_PARITY]` |
| `SnowflakeSession` | untouched — same read-only guard, tag, timeout, row cap |
| `BaselineStore` | snapshots are baselines named `parity/{workbook-stem}/{ds}/{relation}` |
| `verdict.py`, `report/*` | untouched — RunResult in, verdict/reports out |

## 3. Decisions & tradeoffs

| # | Decision | Alternatives rejected | Rationale | Reversibility |
|---|---|---|---|---|
| D1 | Host in **Plumb** as a check family | New repo; host in Autopilot | Plumb already has the Snowflake read-only session, baseline store, verdict/coverage engine, report writers, CI surface. A new repo duplicates all of it; Autopilot has no warehouse access by design. Also avoids installing Autopilot's top-level `core` package (name collision risk). | costly later — decide now |
| D2 | **Table-level** parity in v1; calc compilation deferred | Compile Tableau calcs → SQL | Calc compiler is the hardest engineering (LOD, table calcs, blending) and the estate mix is unmeasured. Table parity catches the dominant migration failures (missing rows, wrong joins in the new layer, type/null drift) at ~20% of the effort. Coverage statement makes the gap honest. | cheap — v2 adds family checks |
| D3 | **Snapshot/compare** as the primary mode; both-live = snapshot+check back-to-back | Both-live only | Galaxy lands incrementally; legacy may be decommissioned before galaxy is complete. Snapshot-now/compare-later works in every ordering and reuses one code path. | cheap |
| D4 | Snapshots stored via existing **BaselineStore** (parquet+manifest), namespaced names | New snapshot format | Zero new storage code, shared-store support (ADR-0012) for free, one less format to QC. | cheap |
| D5 | Old→new object **mapping is an explicit YAML file** (`galaxy-map.yml`), identity by default | Infer mapping by name similarity | The presentation layer renames things; fuzzy inference is the classic silent-corruption source. Plumb's stance is report-don't-guess (same as Autopilot's swap). Unmapped object = FAIL with a named object, never a guess. | cheap |
| D6 | Joins/unions/custom-SQL-with-multiple-tables: **custom SQL is snapshotted as-is** (it's already a SELECT we can wrap); join/union relations are **refused → coverage SKIP** | Decompose joins into base tables | Decomposition changes grain and silently lies about parity. Custom SQL can be run verbatim on both sides (with mapped identifiers when trivially qualifiable, else verbatim + flagged). Mirrors Autopilot's refusal of join/union remap. | cheap |
| D7 | Per-column metrics from **INFORMATION_SCHEMA discovery** at run time: row count, numeric SUM/MIN/MAX, null count per column, COUNT DISTINCT on declared keys, optional grain-grouped counts (top-N, capped) | Full row-hash diff | Row-hash needs ORDER BY total determinism + huge result movement; aggregates are O(1) result size, run warehouse-side, and respect Plumb's row cap. Tolerances handle float drift. | cheap — add a hash check later |
| D8 | One `ctx.session` = the side being queried **now**; the other side is always the snapshot store | Two live sessions in one run | Keeps `CheckContext` contract unchanged; both-live mode is just two phases internally. Simpler read-only audit story: one connection per run. | cheap |
| D9 | New CLI **sub-app `plumb parity`** (snapshot/check) rather than overloading `plumb check parity` | Extend `check` kind | Parity needs two-phase verbs and different options (map, snapshot names). A sub-app keeps `check`'s contract stable. Exit codes identical (0/1/2/3). | cheap |
| D10 | Autopilot integration = **documentation + JSON contracts only** (no imports, no subprocess coupling) | Import Autopilot core; orchestrate its CLI | Different venvs, top-level `core` package name, separate release cadence. The migration runbook documents the swap step; parity neither requires nor wraps it. | cheap |
| D11 | Check IDs `M-*`, family `MIGRATION_PARITY`, severities: structural = BLOCKER, value drift = HIGH, advisory = MEDIUM | — | Matches S-/D-/T-/R-/P- conventions; verdict math unchanged. | cheap |

## 4. Check catalog (v1)

| ID | What it proves | Severity | Status semantics |
|---|---|---|---|
| M-SRC-001 | Workbook's relations are extractable and parity-eligible | HIGH | FAIL if zero eligible relations; WARN if some refused (join/union/extract-only) — each named; SKIP only if no workbook |
| M-MAP-001 | Every eligible relation has a mapping target (or identity) | BLOCKER | FAIL names each unmapped object |
| M-SNAP-001 | A snapshot exists for every eligible relation (check phase) | BLOCKER | FAIL names missing snapshots + the snapshot command to run |
| M-SCHEMA-001 | Mapped object exists on the target side; required columns present, types compatible | BLOCKER | per-relation results |
| M-ROW-001 | Row count parity within `tolerance_pct` (default 0.0) | BLOCKER | observed/expected counts in evidence |
| M-AGG-001 | Numeric column SUM/MIN/MAX parity within `tolerance_pct` (default 0.01) | HIGH | worst offenders in evidence (capped) |
| M-NULL-001 | Per-column null-count parity within tolerance | HIGH | |
| M-DIST-001 | COUNT DISTINCT parity on declared key columns | HIGH | runs only if keys declared in map.yml |
| M-GRAIN-001 | Grouped row counts on declared grain keys match (top-N groups, capped) | HIGH | runs only if grain declared |

Every metric query: built with quoted identifiers via sqlglot, tagged
`plumb_qc:{run_id}`, read-only-guarded, row-capped, statement-timeout enforced —
inherited, not reimplemented.

## 5. map.yml schema (galaxy-map.yml)

```yaml
version: 1
defaults: { tolerance_pct: 0.01 }
objects:
  - old: LEGACY_DB.SALES.ORDERS            # as referenced by the workbook
    new: GALAXY_DB.PRESENTATION.FCT_ORDERS
    keys: [ORDER_ID]                        # → M-DIST-001
    grain: [ORDER_DATE, REGION]             # → M-GRAIN-001
    columns:                                # optional renames, old: new
      REGION: SALES_REGION
    tolerance_pct: 0.0                      # per-object override
  # unlisted objects resolve identity (same FQN both sides)
ignore:
  - LEGACY_DB.SCRATCH.*                     # glob; reported in coverage, not failed
```

Strict parsing (pydantic, forbid unknown keys, loud errors) — same policy as
plumb/config.

## 6. Backlog — epics → stories → tasks

### EPIC E1 — Foundations (engine plumbing + fixtures)
- **S1.1 Engine plumbing for the new family** — tasks: add `CheckFamily.MIGRATION_PARITY`;
  add `parity` target kind + family mapping in engine/runner.py; define the
  `parity_bundle` extras contract (typed dataclass in plumb/parity/contracts.py);
  contract tests. _AC: existing 100% of tests still pass; new family invisible to
  sql/tableau runs._
- **S1.2 Workbook fixtures** — tasks: build tests/fixtures .twb set: (a) two
  single-table Snowflake relations, (b) custom SQL, (c) a join relation (refusal
  path), (d) extract-only datasource (refusal), (e) malformed XML. _AC: fixtures
  load via existing parse path; documented in the fixture module._

### EPIC E2 — Source extraction & mapping
- **S2.1 `plumb/parity/sources.py`** — extract `SourceRelation` per datasource:
  kind (table | custom_sql | refused:join | refused:union | refused:extract),
  database/schema/table or SQL text, connection class. Reuses/extends
  checks/_tableau.py parsing; never a second XML loader. _AC: all five fixtures
  produce exactly the expected relation sets; refusals carry machine-readable
  reasons._
- **S2.2 `plumb/parity/mapping.py`** — load/validate map.yml (pydantic strict);
  resolve relation → target object + column map + keys/grain/tolerance; identity
  fallback; ignore-globs; unmapped detection. _AC: invalid yaml fails loud with
  path+reason; identity and override paths both tested._

### EPIC E3 — Metrics & snapshot pipeline
- **S3.1 `plumb/parity/metrics.py`** — column discovery via INFORMATION_SCHEMA
  through ctx.session; metric SQL builder (row count, numeric aggs, null counts,
  distinct on keys, grain top-N) with sqlglot-quoted identifiers; custom-SQL
  wrapping (`SELECT … FROM ( <user sql> )`); result normalization to a flat
  metric dict. _AC: generated SQL passes assert_read_only; deterministic ordering;
  unit-tested against RouteSession fakes._
- **S3.2 Snapshot store integration (`plumb/parity/runner.py` part 1)** — run
  metrics on the live side, persist one Baseline per relation under
  `parity/{wb-stem}/{ds}/{object}`; manifest carries map version + ruleset
  version + side label. _AC: snapshot → load round-trips losslessly; re-snapshot
  overwrites atomically._

### EPIC E4 — Parity checks + verdict integration
- **S4.1 `plumb/checks/parity.py`** — M-SRC-001, M-MAP-001, M-SNAP-001,
  M-SCHEMA-001, M-ROW-001, M-AGG-001, M-NULL-001, M-DIST-001, M-GRAIN-001 per
  catalog; SKIP semantics for static-only/no-session runs; evidence capped &
  PII-free (aggregates only — no row samples needed). _AC: each check unit-tested
  for pass/fail/skip/tolerance-edge; severities per catalog._
- **S4.2 rules/plumb.yml + verdict wiring** — declare M-* specs with params;
  coverage shows refused relations as skipped checks with reasons. _AC: a run
  with one refused join shows it in Coverage; verdict math unchanged elsewhere._

### EPIC E5 — CLI, reports, docs
- **S5.1 `plumb parity` sub-app** — `snapshot` + `check` commands per §1;
  `--profile`, `--map`, `--out`, `--fail-on`; exit codes 0/1/2/3; `--static-only`
  (extraction+mapping checks without a session). _AC: end-to-end on fixtures with
  RouteSession; JUnit/JSON/HTML written; helpful errors, no tracebacks._
- **S5.2 Docs** — README section, RUNBOOK migration play (snapshot → Autopilot
  swap → check), map.yml reference, ADR-0013 (this plan's D1–D11). _AC: a new
  analyst can run the loop from docs alone._

### EPIC E6 — QC wave & hardening (every sprint, finalized here)
- **S6.1 QC review** — independent QC agent reviews diff for: read-only holes,
  identifier-injection in metric SQL, tolerance math, coverage honesty, contract
  drift. Every finding → a regression test, then the fix.
- **S6.2 Full gates** — pytest, ruff, mypy all green; live smoke vs real
  Snowflake if/when a profile is available (else RouteSession E2E stands in,
  recorded as [assumed] in HANDOVER).

## 7. Sprint plan & team protocol

| Sprint | Scope | Team |
|---|---|---|
| 1 | E1 + E2 | lead (engine plumbing + fixtures) ∥ agent A (sources) ∥ agent B (mapping) → integrate → QC mini-pass |
| 2 | E3 + E4 | lead (runner + rules wiring) ∥ agent C (metrics) ∥ agent D (checks) → integrate → QC mini-pass |
| 3 | E5 + E6 | lead (CLI/docs) ∥ QC agent (full review) → fix loop until green → ship |

**Protocol (proven on Autopilot):** each builder agent owns exactly its module +
its test file, zero shared files; lead owns integration files; QC agents never
write code, only findings; every QC finding gets a pinned regression test before
the fix; a sprint closes only when `pytest -q`, `ruff check plumb tests`, and
`mypy plumb` are all green.

**Definition of Done (per story):** typed, tested (happy + refuse/skip/error
paths), lint+mypy clean, read-only proof intact, coverage statement honest, docs
updated where user-facing.

## 8. Risks

- **Workbook relation XML variants** (Tableau versions, federated connections).
  Mitigation: fixture-first; refuse-and-report anything unrecognized (never guess).
- **INFORMATION_SCHEMA case/quoting drift** between legacy and galaxy. Mitigation:
  canonical upper-case unquoted match first, exact-quoted fallback, M-SCHEMA-001
  surfaces mismatch explicitly.
- **Float tolerance theater** — tolerances hiding real drift. Mitigation: default
  0.0 for row counts; tolerance always printed in observed/expected.
- **No live Snowflake in the build environment** — E2E rides RouteSession fakes;
  the live smoke is a documented post-ship step with the team's profile.

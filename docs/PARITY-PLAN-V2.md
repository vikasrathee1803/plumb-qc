# Migration Parity Validator — Plan of Record v2

_2026-06-09 · Status: DRAFT FOR REVIEW · Continues: docs/PARITY-PLAN.md + ADR-0013_

---

## 1. Product statement

The team is migrating an estate of workbooks to the galaxy/UDM presentation
layer this quarter. Every workbook must be re-pointed and proven. v2 exists to
make that estate-scale: one command proves a whole migration wave, not one
workbook at a time.

### What v1 proved

v1 proved the core loop is correct and production-grade: the snapshot/check
protocol works, the M-* check family integrates cleanly with Plumb's verdict
engine, the 680-test suite is stable, and QC (17 findings) hardened the edge
cases. What v1 did not tackle — by design — is running against more than one
workbook at once, checking calc-level output, or handling the post-swap
artifact. Those gaps are now the migration team's daily friction; v2 removes
them in priority order.

---

## 2. Scope — candidates ranked

### BUILD (this quarter, ordered)

**1. Estate runner** (a) — HIGHEST PRIORITY.
The migration wave is N workbooks, not one. Today a wave requires N manual
`snapshot` calls, N manual `check` calls, and hand-aggregated verdicts. The
estate runner takes a manifest (glob or YAML list), runs snapshot or check on
each workbook, and emits a roll-up report (READY/REVIEW/BLOCKED per workbook
+ estate-level BLOCKED if any). This is the single change that moves the team
from per-workbook ritual to a CI gate on the whole wave. Build first.

**2. Both-live convenience** (e) — MEDIUM-HIGH.
`plumb parity run --workbook X --map M` runs snapshot then check back-to-back
against two connection profiles in one invocation. The internals already do
this (two phases), the CLI doesn't surface it. Very cheap to build on top of
the estate runner (estate runner can also accept `run` as a phase). Unblocks
analysts who have simultaneous legacy + galaxy access today and want a single
command. Build alongside the estate runner as a single epic.

**3. Post-swap check mode** (c) — MEDIUM.
The v1 runbook says: run `check` against the pre-swap workbook. This is
correct but brittle — analysts inevitably swap first and then wonder why check
reports every renamed object as a missing snapshot. Post-swap mode derives the
correct legacy FQNs from the swapped workbook by inverting the map (new→old),
so `check` works on either the pre-swap or the post-swap artifact. Requires
the map inversion to be injective (no two old objects map to the same new
object — already enforced structurally by the YAML schema; enforce in
validation). Surfaces as `--post-swap` flag on `plumb parity check` and on
the estate runner's `check`/`run` modes.

**4. Custom-SQL column metrics via sqlglot projection parsing** (g) — MEDIUM.
v1 only row-counts custom SQL relations — documented as an honest gap. sqlglot
can parse a projection list from well-formed SELECT statements and extract
column aliases. For each projected expression: if it's a plain column
reference, add it to column-metrics discovery; if it's an aggregate, skip
(aggregates of aggregates are wrong). This gets column/null/distinct metrics
on many real custom SQL relations for free. Refuse-with-coverage-note on
unparseable SQL. This is a self-contained extension of `metrics.py` and
closes the estate's biggest coverage gap without any contract changes.

### DEFER (post-quarter, or after estate is validated)

**5. Calc-level reconciliation** (b) — DEFER.
Compiling SIMPLE Tableau calcs (plain SUM/COUNT/AVG/MIN/MAX over mapped
columns, optional WHERE from simple filter predicates) to SQL is valuable but
not the quarter's blocker: the checks that already exist (row, agg, null,
distinct, grain) catch the dominant migration failures. Calc compilation adds
coverage honesty for computed measures. Defer until: (i) the estate runner
has run the wave and the uncovered calc set is measured, and (ii) the LOD/
table-calc/blend refusal surface is understood from real workbooks. Starting
now would be designing for an unmeasured problem. When built: refuse LOD,
table calcs, and blends explicitly with machine-readable reasons, report
coverage as "calc-level: N of M provable"; check IDs M-CALC-001 / M-CALC-ERR-001.

**6. Row-hash deep compare** (d) — ~~DEFER~~ **BUILT 2026-06-10.**
Useful for keyed dimension tables where SUM/MIN/MAX proves little. Cost:
ORDER BY determinism, result movement (capped, but still N rows over the
wire), key declaration required. Defer until a real workbook wave surfaces
tables where aggregate metrics pass but row-level drift is suspected.
When built: capped (default 1 000 rows), keyed only, hash-and-compare server-
side to minimize data movement; check ID M-HASH-001.

> **Amendment (2026-06-10, build):** built ahead of M-CALC by user
> decision (cheaper, closes the scariest silent-failure class). As built:
> `TO_VARCHAR(HASH(<all columns>))` per row, keyed window ordered by the
> declared keys, `--hash-cap` (default 1000, 0 disables) on every parity
> command; only hashes cross the wire. Hashes compare only when both
> sides fingerprinted the same logical column set (schema drift WARNs,
> never fakes row drift); window membership drift WARNs (M-ROW/M-DIST own
> count signals); a non-unique declared key is a named capture error;
> pre-hash snapshots WARN with re-snapshot advice. Codec stays v1
> (additive record kinds). Live-verified vs PORTFOLIO_DEMO_DB: 1000-key
> capped window PASS on V_CUSTOMER_LTV.

### REJECT

**7. Tableau Cloud pull via `--from-cloud`** (f) — REJECT (respect ADR D10).
tableauserverclient has a different release cadence, requires REST credentials
separate from the Snowflake profile, and pulling workbooks is already
Autopilot's job (`tableau-autopilot download`). Importing TSC into Plumb
couples the two tools' release cadences and credential models. ADR D10 is
explicit: Autopilot integration = documentation only. The right answer is:
Autopilot downloads the .twbx, estate runner consumes it from a local path.
Document the two-step in the RUNBOOK (already started). Do not build.

---

## 3. Architecture deltas

Only what changes per built item. The existing component graph (PARITY-PLAN §2)
is unchanged except as noted.

| Item | contracts.py | runner.py | checks/parity.py | CLI (plumb/cli.py) | New files |
|---|---|---|---|---|---|
| Estate runner | Add `EstateResult` (per-workbook RunResult list + roll-up verdict) | New `run_estate()` entry point; parallelism = sequential first, `--workers N` in a later story | M-ESTATE-001 (roll-up; BLOCKER if any BLOCKED workbook) | New `plumb parity estate` subcommand; accepts `--manifest` glob or YAML; `--phase snapshot|check|run`; roll-up HTML report | `plumb/parity/estate.py` |
| Both-live | No change | `run_both_live()` = `run_snapshot()` then `run_check()` on same bundle prefix | No new check | `plumb parity run` alias; `--connection-legacy` + `--connection-target` | None (logic in runner.py) |
| Post-swap | Add `post_swap: bool` to `ParityBundle`; add `map_inverse` helper to `mapping.py` | `build_bundle_post_swap()` inverts map before relation→target resolution | M-MAP-001 evidence updated: names the inversion failure clearly | `--post-swap` flag on `check` and `estate` | None |
| Custom-SQL columns | No change | No change | M-AGG/NULL coverage expands to custom SQL when parseable | No change | `plumb/parity/sql_projection.py` (sqlglot projection extractor; isolated) |

**New check IDs:**

| ID | What it proves | Severity |
|---|---|---|
| M-ESTATE-001 | Roll-up: at least one workbook in the estate is BLOCKED or errored | BLOCKER |
| M-ESTATE-002 | Roll-up: at least one workbook needs review (FAIL on REVIEW, WARN on READY_WITH_NOTES) | HIGH |
| M-CALC-001 | (deferred) Compiled calc output parity within tolerance | HIGH |
| M-HASH-001 | (deferred) Keyed row hash parity, capped | HIGH |

All existing M-* check IDs and severities are unchanged.

> **Amendment (2026-06-10, build):** M-ESTATE-002 was added during the
> build. A single BLOCKER check cannot express D17's "estate is REVIEW
> when any workbook is REVIEW" through engine/verdict.py (the only place
> verdict logic lives); the 001/002 pair makes compute_verdict reproduce
> the D17 roll-up exactly. See ADR-0015.
>
> **Amendment (2026-06-10, QC wave):** the table's "Post-swap" row said
> ParityBundle gains a `map_inverse` field. It does not: post-swap
> resolution happens in mapping.resolve_post_swap (matching relations
> against `new:` directly), because a plain forward resolve over the
> inverted map would key snapshots on the NEW names and never find them.
> `invert_map` exists as a public, tested helper for map tooling, but the
> product path does not call it. ParityBundle gained `post_swap` and
> `map_new_fqns` (the --post-swap hint index) instead.

---

## 4. Decisions & tradeoffs

| # | Decision | Alternatives rejected | Rationale | Reversibility |
|---|---|---|---|---|
| D12 | **Estate runner is sequential** in v1 (no parallel Snowflake sessions) | Thread-pool / asyncio fan-out | Each workbook shares one connection profile; parallel queries on one session multiplexes connections unexpectedly and makes the audit tag story (plumb_qc:{run_id}) ambiguous. Sequential is honest about duration; `--workers N` is a follow-on once the concurrency model is understood. | cheap — add workers later |
| D13 | **Estate manifest** accepts both a glob pattern and an explicit YAML list | Glob-only | A glob cannot express per-workbook map overrides (different map files per workbook set). YAML manifest (`workbooks: [{path, map, keys...}]`) subsumes the glob case; glob is syntactic sugar that expands to identity-map entries. | cheap |
| D14 | **Post-swap mode inverts the map** at load time; the rest of the pipeline is unchanged | Separate snapshot namespace; second map file | Inversion is O(map size), deterministic, and requires the map to be injective (validated on load). A second map file is a maintenance burden; a separate namespace would require renaming all existing snapshots. | cheap |
| D15 | **Custom SQL projection parsing** uses sqlglot; refuses on parse error | regex extraction; ast module | sqlglot handles Snowflake dialect quoting correctly; regex is the classic injection surface; `ast` doesn't parse SQL. On parse failure: fall back silently to v1 row-count-only behavior (do not fail the run — the v1 coverage gap was acceptable, keep it acceptable on complex SQL). | cheap |
| D16 | **Both-live is a CLI alias**, not a new code path — `run` = `snapshot` + `check` on the same snapshot prefix | Dedicated both-live runner with two simultaneous sessions | D8 (one session per run) stands. Two phases, two sessions opened in sequence, is the correct model; it already works via two CLI invocations. The `run` alias is a convenience, not a new architecture. | cheap |
| D17 | **Roll-up verdict**: estate is BLOCKED if ANY workbook is BLOCKED; REVIEW if any REVIEW; READY only if all READY | Fail-safe threshold (e.g. >10% blocked) | A threshold lets migration waves pass with silent drift on a subset of workbooks. Plumb's stance is explicit: every blocked workbook is named in the roll-up report. The team can decide to proceed; the tool must not decide for them. | cheap |
| D18 | **`--post-swap` is opt-in**, not auto-detected | Auto-detect by comparing workbook FQNs to map entries | Auto-detection would silently change behavior when a workbook has been partially swapped. Explicit flag keeps the audit trail clear: the analyst states which artifact they are checking. | cheap |

---

## 5. Backlog

### EPIC E7 — Estate runner + both-live convenience

- **S7.1 `plumb/parity/estate.py` + `EstateResult` contract**
  Tasks: define `EstateManifest` (pydantic: list of `WorkbookEntry` with path,
  map, optional snapshot_prefix override); define `EstateResult` in contracts.py
  (list of per-workbook `WorkbookParity` + roll-up verdict + timestamp); implement
  `run_estate(manifest, phase, session)` calling existing `run_snapshot` /
  `run_check` per entry; emit roll-up.
  AC: 3-workbook fixture manifest produces correct roll-up; one BLOCKED workbook
  makes estate BLOCKED; errors per workbook do not abort the rest.

- **S7.2 M-ESTATE-001 check + rules/plumb.yml entry**
  Tasks: register M-ESTATE-001 (BLOCKER); evidence names each blocked/errored
  workbook with its path and verdict; SKIP outside estate runs.
  AC: unit-tested for all-pass, mixed, all-fail, and empty-manifest cases.

- **S7.3 `plumb parity estate` CLI subcommand**
  Tasks: `--manifest` (glob or YAML path); `--phase snapshot|check|run`;
  `--map` default (overridden per entry by manifest); `--out`; `--fail-on`;
  roll-up HTML report (table of workbook→verdict + estate summary);
  JUnit output (one test case per workbook).
  AC: end-to-end on fixture manifests; roll-up HTML written; JUnit CI-compatible;
  `plumb parity run` alias wires both-live (snapshot then check, same prefix,
  `--connection-legacy` + `--connection-target`).

- **S7.4 RUNBOOK update**
  Tasks: add "wave migration play" section (build manifest, run estate snapshot,
  Autopilot swap wave, run estate check, read roll-up); update the single-workbook
  play to note that `run` alias exists for same-session both-live.
  AC: a new analyst can run an estate migration from docs alone.

### EPIC E8 — Post-swap check mode

- **S8.1 Map inversion + `--post-swap` validation**
  Tasks: `mapping.py` gains `invert_map(ObjectMap) -> ObjectMap`; validates
  injectivity (two old→same new is a map authoring error, not a runtime surprise);
  add `post_swap: bool` and `map_inverse` to `ParityBundle`.
  AC: injective map inverts correctly; non-injective map raises loud config error
  naming the collision; round-trip: invert(invert(M)) == M.

- **S8.2 `--post-swap` flag on `parity check` and `parity estate`**
  Tasks: pass flag through CLI → runner → `build_bundle_post_swap()`; update
  M-MAP-001 evidence to distinguish "unmapped" from "inversion failed"; RUNBOOK
  adds post-swap play.
  AC: fixture with swapped workbook (new FQNs) + `--post-swap` produces same
  verdict as pre-swap workbook without the flag; missing `--post-swap` on a
  swapped workbook fails M-MAP-001 with a named suggestion in evidence.

### EPIC E9 — Custom-SQL column metrics

- **S9.1 `plumb/parity/sql_projection.py`**
  Tasks: `extract_projected_columns(sql: str) -> list[str] | None`; uses sqlglot
  with Snowflake dialect; returns column names (aliases where present, expression
  text otherwise) for plain column refs and non-aggregate expressions; returns
  `None` on parse failure or when the projection contains `*`; unit-tested
  against fixture SQL variants (simple SELECT, aliased columns, mixed
  aggregates, unparseable).
  AC: parse failure returns None (never raises); `*` returns None; plain columns
  returned correctly; aggregate-only projections return empty list (→ row-count
  only, same as v1).

- **S9.2 Wire projection parsing into `metrics.py`**
  Tasks: in the custom SQL branch of `measure()`, call
  `extract_projected_columns`; when non-None and non-empty, add the resulting
  columns to the INFORMATION_SCHEMA discovery query scope (scoped to the
  wrapping CTE); run null-count and SUM/MIN/MAX on discovered numeric columns.
  AC: custom SQL fixture with parseable projection produces column metrics in
  ParityMetrics; unparseable fixture still produces row-count-only (no error,
  coverage note); M-AGG-001 and M-NULL-001 fire on custom SQL relations when
  columns are discoverable.

### EPIC E10 — QC wave v2

- **S10.1 QC review**
  Scope: estate result serialization (JSON/parquet round-trip); map inversion
  correctness; post-swap identifier matching; custom-SQL injection surface
  (sqlglot output still goes through `_quote_ident`); coverage honesty on
  unparseable SQL; roll-up verdict math edge cases; `--workers` flag absent
  = safe.
  Every finding → regression test before fix. Gates: pytest, ruff, mypy all
  green before S10.1 closes.

---

## 6. Risks + scope cuts

| Risk | Mitigation | Cut trigger |
|---|---|---|
| Estate runner reveals XML variants not in fixtures (federated, Tableau Bridge connections) | refuse-and-report per workbook; never abort the estate run; add variants as fixtures on first encounter | If >30% of the estate's workbooks are refused by sources.py → skip E9, prioritize sources.py fixture expansion |
| Map inversion is non-injective in the real estate (two legacy tables merged into one galaxy table) | validation error names the collision; team must split the map by wave | If many-to-one merges are common → post-swap mode is lower value; defer E8 |
| sqlglot parse failures on the estate's custom SQL are widespread | fall back to v1 row-count-only silently; coverage note makes the gap visible | If >50% of custom SQL fails to parse → E9 provides little value; defer |
| Both-live requires simultaneous READ on legacy (which may be decommissioned mid-wave) | snapshot first, legacy gone, check still works | No change needed — the snapshot/check split already handles this |
| Quarter deadline pressure | E7 (estate) is the must-have; E8 and E9 are cut if E7 slips past the midpoint | Cut E8+E9 if E7 S7.3 is not green by mid-quarter |

**Non-negotiable for this quarter:** E7 (estate runner, both-live, CLI, roll-up
report). Everything else is bonus. The migration wave cannot wait for calc-level
or post-swap polish.

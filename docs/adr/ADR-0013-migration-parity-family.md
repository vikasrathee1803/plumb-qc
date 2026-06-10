# ADR-0013: Migration parity check family

Date: 2026-06-09 · Status: accepted · Plan of record: ../PARITY-PLAN.md

## Context

The team is migrating to the UDM / galaxy warehouse with a new presentation
layer. Every Tableau workbook must be re-pointed and proven to show the same
numbers. Plumb gains a check family that snapshots parity metrics on the
legacy side and compares the migrated side against them.

## Decisions (D1–D11 from the plan of record, recorded for permanence)

1. **Hosted in Plumb** as `CheckFamily.MIGRATION_PARITY` with target kind
   `parity` — reuses the read-only session, baseline store, verdict engine,
   and report writers. Not a new repo; not in Tableau Autopilot (no
   warehouse access there by design).
2. **Table-level parity in v1** (row counts, per-column aggregates, null and
   distinct counts, grain-grouped counts). Compiling Tableau calculations to
   SQL is deferred; the coverage statement keeps the gap honest.
3. **Snapshot/compare is the primary mode.** The galaxy side lands
   incrementally, so legacy is measured when it is available and compared
   later. Both-live is just the two phases back to back.
4. **Snapshots are Baselines** in the existing parquet+manifest store under
   flat names `parity__{workbook}__{datasource}__{object}`; the shared-store
   mechanism (ADR-0012) applies unchanged.
5. **Old→new mapping is an explicit YAML file**, strict-parsed, identity
   fallback by default, ignore globs, never fuzzy matching. An unmapped
   object is a named BLOCKER (M-MAP-001), not a guess.
6. **Joins and unions are refused**, not decomposed (decomposition changes
   grain and lies). Custom SQL is snapshotted verbatim — it is already a
   SELECT that runs on both sides. Extract-only datasources are refused;
   extracts OVER live relations are eligible (parity is proven against the
   warehouse objects the extract refreshes from).
7. **Aggregates, not row hashes.** O(1) result size, warehouse-side compute,
   row-cap friendly; tolerances handle float drift (row tolerance defaults
   to 0.0, aggregate tolerance to 0.01, both overridable per object).
8. **One live session per run.** The other side is always the snapshot
   store; `CheckContext` is unchanged. The M-* checks are pure comparisons
   over a ParityBundle the runner assembles — each metric query runs exactly
   once per run.
9. **CLI is a sub-app** (`plumb parity snapshot|check`) with the standard
   exit codes; `--connection` allows a different connection profile per side.
10. **Tableau Autopilot integration is documentation only** — the runbook's
    swap step. No imports, no subprocess coupling.
11. **M-* severities**: structural gaps (MAP/SNAP/SCHEMA/ROW) are BLOCKER,
    value drift (AGG/NULL/DIST/GRAIN) is HIGH, eligibility (SRC) is HIGH.
    MIGRATION_PARITY ranks first in coverage risk order: a skipped parity
    check during a migration is exactly the silent drift Plumb exists to
    prevent.

## Consequences

- A snapshot run's verdict reports capture completeness (M-SNAP-001 verifies
  the writes that just happened); a failed measurement is never invisible.
- All metric SQL is built from untrusted workbook/map identifiers and is
  therefore quoted/escaped and gated by assert_read_only like every other
  Plumb query (tag, timeout, row cap inherited).
- v2 candidates: calc-level reconciliation (compiling simple aggregate
  calcs), a row-hash deep-compare check, both-live convenience command.

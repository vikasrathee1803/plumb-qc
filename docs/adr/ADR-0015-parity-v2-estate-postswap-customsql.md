# ADR-0015: Parity v2 — estate runner, post-swap mode, custom-SQL columns

Date: 2026-06-10. Status: accepted.

Parity v1 (ADR-0013-migration-parity-family) proved one workbook per
invocation. The migration wave is N workbooks, analysts swap artifacts
before checking them, and custom SQL carried row-count-only coverage.
PARITY-PLAN-V2 ranks the fixes; its §4 table (D12–D18) is the decision
record of record. This ADR pins the points that constrain future work.

**D12 — the estate runner is sequential.** One connection profile per
side, one session per sweep, every workbook sharing the run's
plumb_qc:{run_id} QUERY_TAG. Parallel fan-out is a follow-on once the
concurrency and audit-tag story is understood. Reversibility: cheap.

**D13 — the manifest is YAML or a glob.** A glob cannot express
per-workbook maps; the YAML manifest subsumes it (a glob expands to
entries that use the --map default). Manifest-relative paths; duplicate
snapshot prefixes (same file stem) are refused at load. Reversibility:
cheap.

**D14/D18 — post-swap inverts the map, opt-in only.** `--post-swap`
derives each relation's legacy snapshot identity by applying the map
new→old. Inversion demands fully-qualified, injective `old:`/`new:`
pairs and is validated loudly at resolve time — NOT at map load, because
many-to-one maps stay legal for forward checking. Auto-detecting
swapped workbooks was rejected: a partially-swapped artifact would
silently change behavior; the analyst states what they are checking.
The checks may *suggest* the flag (M-MAP-001/M-SNAP-001 remediation
when an unmapped or snapshot-less relation carries a `new:` name) but
never set it. Reversibility: cheap.

**D15 — custom-SQL projections parse with sqlglot, refuse silently.**
Parse failure, stars, non-SELECT, or aggregate-only projections fall
back to v1 row-count-only behavior without failing the run — the v1
coverage gap was acceptable and stays acceptable on complex SQL. All
projected identifiers pass through the same quoting as table columns.
Reversibility: cheap.

**D16 — both-live is a CLI alias, not an architecture.** `plumb parity
run` (and `--phase run` on the estate) is snapshot-then-check with two
sessions opened in sequence, never simultaneously; D8 (one session per
run) stands. A BLOCKED snapshot phase skips the check phase.
Reversibility: cheap.

**D17 — the roll-up names offenders, never thresholds.** Estate verdict:
BLOCKED if any workbook is blocked or errored, REVIEW if any REVIEW,
READY only when all READY. The team may ship anyway; the tool does not
decide for them. Reversibility: cheap.

**Amendment to the plan: M-ESTATE-002.** The plan's check table listed
only M-ESTATE-001 (BLOCKER). A single BLOCKER check cannot express
"estate is REVIEW when any workbook is REVIEW" through the engine's
verdict rules, and engine/verdict.py is the only place verdict logic may
live. So the roll-up is two checks: M-ESTATE-001 (BLOCKER; fails on any
blocked/errored workbook) and M-ESTATE-002 (HIGH; fails on any REVIEW
workbook, warns on any READY_WITH_NOTES). compute_verdict over the pair
reproduces D17 exactly — pinned by a test
(test_engine_verdict_equals_d17_rollup). EstateResult.compute_rollup()
is pure aggregation of already-computed verdicts for the CLI exit code
and the roll-up report; it introduces no second verdict derivation.

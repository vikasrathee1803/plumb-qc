# BACKLOG

Ordered. Phase gates are hard stops.

## Phase 1 (next, on Gate 0 approval)

1. Stream A: sql_static checks (S-LINT-001, S-STAT-001..008, S-STAT-010)
2. Stream A: sql_meta checks (S-META-001..004)
3. Stream B: sql_assertions (D-GRAIN-001/002, D-NULL-001/002, D-RI-001,
   D-DOMAIN-001, D-RANGE-001, D-FRESH-001, D-RECON-001, D-DUP-001,
   D-ADD-001) plus evidence capping and PII redaction
4. Stream C: baseline/store.py (Parquet plus manifest, store interface)
5. Stream C: sql_regression (R-DIFF-001, R-AGG-001)
6. Stream D: sql_performance (P-PROF-001, P-COST-001, P-SPILL-001,
   P-CARD-001)
7. Stream E: report writers (HTML self-contained, JSON, JUnit)
8. Integration: engine/runner.py, audit JSONL record, full CLI surface
   (init, rules pull, check sql, baseline create/update, report open)
9. QUERY_HISTORY verification of tag, warehouse, timeout, row cap
10. Docs: RUNBOOK grants, README usage, fixture corpus
11. Gate 1: full acceptance criteria run, hard stop

## Phase 2 (gated)

- Tableau static checks (T-*), web UI (FastAPI plus React), AI assist
  (explain, fix, recon SQL), shared baseline store, central audit sink

## Phase 3 (explicitly deferred)

- T-RECON-001, T-LINEAGE-001, T-CONSIST-001

## Icebox / open questions

- plumb rules pull transport (git clone vs internal artifact registry)
- Account-level checks via SNOWFLAKE.ACCOUNT_USAGE (latency caveat)
- Trend score storage for verdict-over-time reporting

# parity-smoke — Migration Parity live smoke scripts

This folder contains the artefacts and outputs for the end-to-end live smoke
of the Migration Parity Validator (PARITY-PLAN.md §E6.2).

## Files

| File | Purpose |
|---|---|
| `demo-workbook.twb` | Federated Tableau workbook with two Snowflake relations: `ANALYTICS.V_ORDER_ANALYTICS` and `ANALYTICS.V_CUSTOMER_LTV` in `PORTFOLIO_DEMO_DB`. Shape modelled on `tests/_parity_fixtures.py` TWB_TWO_TABLES. |
| `identity-map.yml` | Parity map v1: each view maps to itself (old == new, 3-part FQNs). V_CUSTOMER_LTV has `keys: [CUSTOMER_ID]` and `grain: [REGION]`; V_ORDER_ANALYTICS has `grain: [CUSTOMER_REGION, CUSTOMER_SEGMENT]`. `tolerance_pct: 0.0` throughout. |
| `drift-map.yml` | Parity map v1: V_ORDER_ANALYTICS is deliberately mapped to `V_PRODUCT_MARGIN` (schema-drift demo — different shape). V_CUSTOMER_LTV stays identity. `tolerance_pct: 0.0` throughout. |
| `cloud/Superstore.twbx` | Tableau Superstore sample workbook downloaded from Tableau Cloud (no Snowflake relations — tests the refused-datasource path). |
| `out-snapshot/` | Reports from Phase A (snapshot run). |
| `out-check-identity/` | Reports from Phase B (check against identity map — all PASS). |
| `out-check-drift/` | Reports from Phase C (check against drift map — BLOCKED). |
| `out-cloud/` | Reports from Phase D (static-only on Superstore.twbx). |

## Three commands (run from repo root)

**Phase A — snapshot the legacy side**

```
.venv\Scripts\plumb.exe parity snapshot \
  --workbook scripts/parity-smoke/demo-workbook.twb \
  --map scripts/parity-smoke/identity-map.yml \
  --out scripts/parity-smoke/out-snapshot
```

Expected: exit 0, verdict READY, M-SNAP-001 PASS "2 snapshot(s) written".

**Phase B — check against identity map (parity proven)**

```
.venv\Scripts\plumb.exe parity check \
  --workbook scripts/parity-smoke/demo-workbook.twb \
  --map scripts/parity-smoke/identity-map.yml \
  --out scripts/parity-smoke/out-check-identity
```

Expected: exit 0, verdict READY, all M-* checks PASS (identical objects,
tolerance 0.0).

**Phase C — check against drift map (schema drift detected)**

```
.venv\Scripts\plumb.exe parity check \
  --workbook scripts/parity-smoke/demo-workbook.twb \
  --map scripts/parity-smoke/drift-map.yml \
  --out scripts/parity-smoke/out-check-drift
```

Expected: exit 2, verdict BLOCKED. M-SCHEMA-001 FAIL naming `V_PRODUCT_MARGIN`
as missing V_ORDER_ANALYTICS's columns. V_CUSTOMER_LTV side still passes all
checks (identity mapping is still correct).

## Expected outcomes (verified live 2026-06-09)

| Phase | Exit | Verdict | Notable checks |
|---|---|---|---|
| A snapshot | 0 | READY | M-SNAP-001 PASS "2 snapshot(s) written" |
| B identity check | 0 | READY | All 9 M-* PASS (39 aggregates, 23 null cols, 1 distinct key, grain matched) |
| C drift check | 2 | BLOCKED | M-SCHEMA-001 FAIL (V_PRODUCT_MARGIN missing 9 columns); M-ROW-001 FAIL (+7895% row delta); M-AGG-001 FAIL (3 aggregate breaches); V_CUSTOMER_LTV side PASS |
| D cloud static | 1 | REVIEW | M-SRC-001 FAIL "0 parity-eligible relations" (Superstore uses Excel/text/extract — correct refusal); no traceback |

Snapshots are stored under `~/.plumb/baselines/` as parquet + manifest pairs.
The warehouse is read-only by design; no writes reach Snowflake beyond SELECT.

## v2 smoke (estate, post-swap, custom-SQL columns) — verified live 2026-06-10

| File | Purpose |
|---|---|
| `estate-manifest.yml` | 2-workbook wave: the demo workbook + a custom-SQL variant. |
| `demo-custom-sql.twb` | Custom-SQL relation over V_CUSTOMER_LTV — exercises the SYSTEM$TYPEOF probe and custom-SQL column metrics (E9) live. |

| Phase | Command | Exit | Verdict |
|---|---|---|---|
| Estate both-live | `plumb parity estate --manifest scripts/parity-smoke/estate-manifest.yml --phase run` | 0 | READY (both workbooks READY in both sweeps; one session per sweep) |
| Custom-SQL columns | `plumb parity check --workbook scripts/parity-smoke/demo-custom-sql.twb --map scripts/parity-smoke/identity-map.yml` | 0 | READY — M-AGG-001 "9 aggregate(s) ... 1 object(s) with column metrics", M-NULL-001 4 columns (probe live-verified, not a row-count fallback) |
| Post-swap | `plumb parity check --workbook scripts/parity-smoke/demo-workbook.twb --map scripts/parity-smoke/identity-map.yml --post-swap` | 0 | READY — all 9 M-* PASS via the inverted-map resolution path |
| Estate drift | `plumb parity estate --manifest "scripts/parity-smoke/demo-workbook.twb" --map scripts/parity-smoke/drift-map.yml --phase check` | 2 | BLOCKED — M-ESTATE-001 FAIL naming the workbook |

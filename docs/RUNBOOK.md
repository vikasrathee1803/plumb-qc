# RUNBOOK

## Snowflake provisioning (the one Phase 1 external dependency)

Run as an admin, adjust schema scope to taste:

```sql
CREATE ROLE IF NOT EXISTS PLUMB_QC_ROLE;
CREATE WAREHOUSE IF NOT EXISTS PLUMB_WH
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;
GRANT USAGE ON WAREHOUSE PLUMB_WH TO ROLE PLUMB_QC_ROLE;
GRANT USAGE ON DATABASE ANALYTICS TO ROLE PLUMB_QC_ROLE;
GRANT USAGE ON ALL SCHEMAS IN DATABASE ANALYTICS TO ROLE PLUMB_QC_ROLE;
GRANT SELECT ON ALL TABLES IN DATABASE ANALYTICS TO ROLE PLUMB_QC_ROLE;
GRANT SELECT ON ALL VIEWS IN DATABASE ANALYTICS TO ROLE PLUMB_QC_ROLE;
GRANT SELECT ON FUTURE TABLES IN DATABASE ANALYTICS TO ROLE PLUMB_QC_ROLE;
GRANT SELECT ON FUTURE VIEWS IN DATABASE ANALYTICS TO ROLE PLUMB_QC_ROLE;
GRANT ROLE PLUMB_QC_ROLE TO USER <analyst_user>;
```

INFORMATION_SCHEMA comes with database USAGE. Do not grant anything
beyond SELECT and USAGE: Plumb is read-only and refuses non-reads anyway.

## Local setup (analyst)

1. pipx install plumb (internal index)
2. Create ~/.plumb/connection.yml:

```yaml
account: "myorg-account"
user: "YOUR_USER"
authenticator: "externalbrowser"   # or snowflake_jwt with private_key_path
role: "PLUMB_QC_ROLE"
warehouse: "PLUMB_WH"
```

3. plumb rules pin 2026.06.0
4. Key-pair users: key at ~/.plumb/keys/, passphrase in the OS keychain
   (service "plumb") or PLUMB_PRIVATE_KEY_PASSPHRASE.

### OAuth setup (the configured method here)

connection.yml uses authenticator: oauth. Provide the token out of band:

```
# PowerShell, current session only
$env:PLUMB_OAUTH_TOKEN = "<your oauth token>"
plumb check sql --query rpt.sql --profile finance
```

Or store it in the OS keychain under service "plumb", entry
"oauth_token:{account}:{user}". The token is never written to a file or a
repo. account, user, role, and warehouse go in ~/.plumb/connection.yml.

### Tableau

```
plumb check tableau --workbook dashboard.twbx --profile finance
```

No Snowflake connection is needed for Tableau static analysis; it parses
the .twb or .twbx locally.

## CI

- Build the image from the Dockerfile; mount the key; gate on exit code
  (0 pass, 1 review, 2 blocked, 3 tool error).

## Cost attribution and verification

Every Plumb query is tagged plumb_qc:{run_id} and runs on PLUMB_WH:

```sql
SELECT query_tag, warehouse_name, total_elapsed_time, rows_produced
FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY())
WHERE query_tag LIKE 'plumb_qc:%'
ORDER BY start_time DESC;
```

Use this exact query for the Gate 1 guardrail verification.

## Troubleshooting

- exit 3 with a pydantic message: the ruleset or connection profile is
  malformed; the message names the field. Plumb never half-runs on bad
  config.
- "ruleset version does not match the pinned version": fetch the pinned
  rules or repin deliberately.
- ReadOnlyViolation: the SQL contains something that is not a single
  SELECT read. That refusal is by design; see ADR-0003.

## Migration parity play (galaxy / UDM cut-over)

Per workbook, three steps; nothing is eyeballed:

1. **Snapshot the legacy side** (while it still exists):
   `plumb parity snapshot --workbook sales.twbx --map galaxy-map.yml`
   The verdict tells you whether capture was complete: M-SNAP-001 fails or
   errors if any source could not be measured or written. Refused sources
   (joins, unions, extract-only, published) appear in coverage — decide
   per case whether they need a manual check.
2. **Check the migrated side — normally against the SAME (pre-swap)
   workbook**:
   `plumb parity check --workbook sales.twbx --map galaxy-map.yml`
   The workbook is the manifest of legacy objects; the map supplies the
   new names. Exit 0 = parity proven (READY). Exit 2 = BLOCKED with the
   drifted objects, columns, and metrics named in the report. Use `--out`
   to keep per-workbook reports; JUnit output slots into CI.

   **Already swapped?** If the workbook has been re-pointed first (its
   relations carry the NEW FQNs), add `--post-swap`: the map is applied
   inverted (new→old) to find the legacy snapshots. This requires every
   mapped `old:` name to be fully qualified, spelled exactly as the
   pre-swap workbook spelled it, unique per `new:` (no two legacy objects
   merged into one target), and the workbook FILE NAME unchanged since the
   snapshot (the snapshot prefix derives from the file stem). Custom SQL
   whose text was edited during the swap cannot be re-identified and
   surfaces as a missing snapshot. Forgetting the flag is safe: the check
   blocks and the report's remediation names `--post-swap` — do NOT
   re-snapshot at that point, that would capture the target side as the
   baseline.
3. **Re-point and publish** once parity is proven: Tableau Autopilot
   (`swap-connection` same-schema, or `plan-swap`/`swap-source` when names
   changed) validates, backs up, and saves atomically; parity never edits
   workbooks.

Have simultaneous read access to both sides? `plumb parity run --workbook
sales.twbx --map galaxy-map.yml --connection-legacy legacy.yml
--connection-target galaxy.yml` does snapshot-then-check in one command
(two sessions opened in sequence, never together); reports land in
`snapshot/` and `check/` under `--out`, and the exit code is the worst of
the two verdicts. If the snapshot phase is BLOCKED the check phase is
skipped — fix the legacy capture first.

The map file (galaxy-map.yml) declares old→new object renames, per-object
keys (distinct-count parity), grain columns (grouped-count parity), column
renames, and tolerances. Unlisted objects compare under their own names
(identity); set `defaults: {identity_fallback: false}` to force every
object to be declared. Snapshots live in the baseline store (shared store
per ADR-0012 works for a whole team).

Declaring `keys:` on an object also buys row-level fingerprints
(M-HASH-001): the first 1 000 rows by key order are hashed server-side on
both sides and compared per key — catching cell drift that every
aggregate can miss (two rows swapping regions leaves counts, distincts,
and sums untouched). Only hashes leave the warehouse. Tune the window
with `--hash-cap N` (0 disables); snapshots taken before keys were
declared WARN with re-snapshot advice rather than overstating proof.

Different accounts for legacy and galaxy? Pass `--connection PATH` to
either phase to use an alternate connection profile file.

Prefer a browser? `plumb web` → Migration tab runs the same
snapshot/check pipeline on a single uploaded workbook (map upload and
post-swap supported); snapshots are shared with the CLI as long as the
workbook keeps the same file name.

## Wave migration play (estate runner)

A migration wave is N workbooks, one command per phase, one roll-up
verdict (PARITY-PLAN-V2 E7):

1. **Build the manifest.** Either a glob —
   `plumb parity estate --manifest "wave1/*.twbx" --map galaxy-map.yml ...`
   (every workbook gets the `--map` default) — or a YAML file when
   workbooks need their own maps:

   ```yaml
   version: 1
   workbooks:
     - path: wave1/sales.twbx        # relative to this manifest file
       map: maps/sales-map.yml
     - path: wave1/finance.twbx      # no map: uses --map, else identity
   ```

   Two workbooks with the same file stem collide on snapshot names; the
   manifest loader refuses them — give one entry an explicit
   `snapshot_prefix`.
2. **Snapshot the wave** while the legacy side exists:
   `plumb parity estate --manifest wave1.yml --phase snapshot --connection legacy.yml`
3. **Swap the wave** with Tableau Autopilot (download → swap → publish;
   parity consumes the local files, never Tableau Cloud directly — ADR
   D10).
4. **Check the wave**:
   `plumb parity estate --manifest wave1.yml --phase check --connection galaxy.yml`
   (add `--post-swap` when checking the swapped artifacts, with the same
   caveats as the single-workbook play). With access to both sides at
   once, `--phase run --connection-legacy ... --connection-target ...`
   does steps 2 and 4 back to back.
5. **Read the roll-up.** Workbooks run sequentially and an error in one
   never aborts the rest. The estate verdict is explicit (D17): BLOCKED if
   ANY workbook is blocked or errored, REVIEW if any needs review, READY
   only when every workbook is READY — there is no percentage threshold;
   every offender is named (M-ESTATE-001/002). `estate.html` is the
   per-workbook table, `estate.junit.xml` (one test case per workbook)
   slots into CI, and `--fail-on` overrides the ruleset gate for the exit
   code.

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
2. **Check the migrated side — against the SAME (pre-swap) workbook**:
   `plumb parity check --workbook sales.twbx --map galaxy-map.yml`
   The workbook is the manifest of legacy objects; the map supplies the
   new names. Do not run `check` on a re-pointed copy: after a rename
   swap its relations carry the new FQNs, so snapshots and map entries no
   longer match and every renamed object reads as a missing snapshot.
   Exit 0 = parity proven (READY). Exit 2 = BLOCKED with the drifted
   objects, columns, and metrics named in the report. Use `--out` to keep
   per-workbook reports; JUnit output slots into CI.
3. **Re-point and publish** once parity is proven: Tableau Autopilot
   (`swap-connection` same-schema, or `plan-swap`/`swap-source` when names
   changed) validates, backs up, and saves atomically; parity never edits
   workbooks.

The map file (galaxy-map.yml) declares old→new object renames, per-object
keys (distinct-count parity), grain columns (grouped-count parity), column
renames, and tolerances. Unlisted objects compare under their own names
(identity); set `defaults: {identity_fallback: false}` to force every
object to be declared. Snapshots live in the baseline store (shared store
per ADR-0012 works for a whole team).

Different accounts for legacy and galaxy? Pass `--connection PATH` to
either phase to use an alternate connection profile file.

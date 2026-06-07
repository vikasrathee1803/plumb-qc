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

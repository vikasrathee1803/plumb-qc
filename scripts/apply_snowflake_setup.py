"""Provision Plumb's least-privilege Snowflake role and warehouse.

This applies scripts/snowflake_setup.sql against your account using the
administrative connection in ~/.plumb/connection.yml (key-pair / SSO). It is
the one place Plumb issues DDL, deliberately outside the read-only engine,
because it is an admin provisioning step, not a QC run.

    python scripts/apply_snowflake_setup.py \
        --database PORTFOLIO_DEMO_DB --schema ANALYTICS --grant-user ANALYST_USER

Everything is idempotent (IF NOT EXISTS / re-runnable grants). Drop it again
with: DROP ROLE PLUMB_QC; DROP WAREHOUSE PLUMB_WH;
"""

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plumb.config.loader import load_connection_profile  # noqa: E402
from plumb.connect.snowflake import build_connect_kwargs  # noqa: E402

ROLE = "PLUMB_QC"
WAREHOUSE = "PLUMB_WH"


def statements(database: str, schema: str, grant_user: str, cortex: bool) -> list[str]:
    fq = f"{database}.{schema}"
    out = [
        f"CREATE ROLE IF NOT EXISTS {ROLE} "
        f"COMMENT = 'Read-only role for Plumb QC. SELECT only.'",
        f"CREATE WAREHOUSE IF NOT EXISTS {WAREHOUSE} "
        "WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE "
        "INITIALLY_SUSPENDED = TRUE COMMENT = 'Dedicated warehouse for Plumb QC reads.'",
        f"GRANT USAGE ON WAREHOUSE {WAREHOUSE} TO ROLE {ROLE}",
        f"GRANT USAGE ON DATABASE {database} TO ROLE {ROLE}",
        f"GRANT USAGE ON SCHEMA {fq} TO ROLE {ROLE}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {fq} TO ROLE {ROLE}",
        f"GRANT SELECT ON FUTURE TABLES IN SCHEMA {fq} TO ROLE {ROLE}",
        f"GRANT SELECT ON ALL VIEWS IN SCHEMA {fq} TO ROLE {ROLE}",
        f"GRANT SELECT ON FUTURE VIEWS IN SCHEMA {fq} TO ROLE {ROLE}",
        f"GRANT ROLE {ROLE} TO USER {grant_user}",
    ]
    if cortex:
        out.append(f"GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE {ROLE}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision Plumb's PLUMB_QC role.")
    ap.add_argument("--database", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--grant-user", required=True, help="User to receive the role.")
    ap.add_argument("--cortex", action="store_true", help="Also grant Cortex usage.")
    ap.add_argument("--dry-run", action="store_true", help="Print the DDL only.")
    args = ap.parse_args()

    ddl = statements(args.database, args.schema, args.grant_user, args.cortex)
    if args.dry_run:
        for s in ddl:
            print(s + ";")
        return 0

    import snowflake.connector

    profile = load_connection_profile()
    kwargs = build_connect_kwargs(profile, run_id=str(uuid.uuid4()), statement_timeout_s=120)
    print(f"Connecting as {profile.user} / role {profile.role} to apply {ROLE} and {WAREHOUSE}...")
    conn = snowflake.connector.connect(**kwargs)
    try:
        cur = conn.cursor()
        for s in ddl:
            cur.execute(s)
            print(f"  ok: {s[:70]}{'...' if len(s) > 70 else ''}")
        print("\nGrants on", ROLE)
        cur.execute(f"SHOW GRANTS TO ROLE {ROLE}")
        for row in cur.fetchall():
            # privilege, granted_on, name are the columns of interest
            print(f"  {row[1]:10} {row[2]:14} {row[3]}")
        cur.close()
    finally:
        conn.close()
    print(
        f"\nDone. Point ~/.plumb/connection.yml at role: {ROLE}, warehouse: {WAREHOUSE} "
        f"for {args.grant_user}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

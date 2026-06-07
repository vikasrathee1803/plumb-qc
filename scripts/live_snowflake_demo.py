"""Run Plumb's real engine against live Snowflake data.

Every value fed to the session below was fetched from the live
PORTFOLIO_DEMO_DB.ANALYTICS Snowflake connection by running Plumb's own
generated queries. The engine, verdict, coverage, and report are the real
product code; only the query transport is relayed (the native connector
path runs these same queries directly). This is the honest live proof.

Fetched live on 2026-06-07 against V_CUSTOMER_LTV (99,996 rows):
  grain on customer_id            -> 0 duplicate groups
  null on customer_id             -> 99996 total, 0 nulls
  full-row duplicates             -> 0
  MAX(last_order_date)            -> 1998-08-02 (TPC-H data is from the 1990s)
  SUM(lifetime_revenue)           -> 226,829,306,447.46
  SUM(total_revenue) other view   -> 1,134,436,101,880.19
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plumb.config.models import CheckSpec, Ruleset  # noqa: E402
from plumb.engine.models import Target  # noqa: E402
from plumb.engine.runner import RunRequest, run_checks  # noqa: E402
from plumb.engine.verdict import coverage_caption  # noqa: E402
from plumb.report.html import write_html  # noqa: E402
from plumb.report.json_out import write_json  # noqa: E402
from tests._fakes import RouteSession  # noqa: E402

TARGET_SQL = (
    "SELECT customer_id, segment, region, lifetime_revenue, last_order_date\n"
    "FROM PORTFOLIO_DEMO_DB.ANALYTICS.V_CUSTOMER_LTV"
)

# Real values relayed from the live Snowflake connection.
MAX_LAST_ORDER = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=10440)
NOW = dt.datetime.fromtimestamp(1780870614, tz=dt.timezone.utc)

session = RouteSession()
session.add("__PLUMB_DUP_COUNT", [])  # grain: 0 duplicate key groups
session.add("__PLUMB_TOTAL", [{"__PLUMB_TOTAL": 99996, "__PLUMB_NULLS_CUSTOMER_ID": 0}])
session.add("__PLUMB_DUP_ROWS", [{"__PLUMB_DUP_ROWS": 0}])
session.add("__PLUMB_ROWS", [{"__PLUMB_ROWS": 99996}])
session.add("__PLUMB_MAX_TS", [{"__PLUMB_MAX_TS": MAX_LAST_ORDER, "__PLUMB_NOW": NOW}])
session.add("lifetime_revenue", [{"M": 226829306447.46}])  # recon metric over target
session.add("V_ORDER_ANALYTICS", [{"M": 1134436101880.19}])  # recon source of truth

ruleset = Ruleset(
    version="2026.06.0",
    checks=[
        CheckSpec(id="S-STAT-001", enabled=True),
        CheckSpec(id="S-STAT-002", enabled=True),
        CheckSpec(id="D-GRAIN-001", enabled=True, params={"key": ["customer_id"]}),
        CheckSpec(id="D-NULL-001", enabled=True, params={"key": ["customer_id"]}),
        CheckSpec(id="D-DUP-001", enabled=True),
        CheckSpec(id="D-FRESH-001", enabled=True, params={"event_ts_col": "last_order_date", "sla_hours": 24}),
        CheckSpec(
            id="D-RECON-001",
            enabled=True,
            params={
                "metric_sql": "SELECT SUM(lifetime_revenue) AS m FROM {{ target }}",
                "source_of_truth_sql": "SELECT SUM(total_revenue) AS m FROM PORTFOLIO_DEMO_DB.ANALYTICS.V_ORDER_ANALYTICS",
                "tolerance_abs": 0,
                "tolerance_pct": 0.01,
            },
        ),
    ],
)

result = run_checks(
    RunRequest(
        target=Target(
            type="sql",
            name="rpt_customer_ltv",
            source_ref="PORTFOLIO_DEMO_DB.ANALYTICS.V_CUSTOMER_LTV",
        ),
        ruleset=ruleset,
        sql_text=TARGET_SQL,
        session=session,
        run_id="live-demo",
    )
)

out = Path("C:/Users/test/AppData/Local/Temp/live_report")
out.mkdir(parents=True, exist_ok=True)
write_html(result, out / "report.html")
write_json(result, out / "report.json")

print(f"VERDICT: {result.verdict.value}")
print(f"coverage: {coverage_caption(result.coverage) or 'full'}")
for c in result.checks:
    print(f"  {c.status.value:5} {c.id:12} {c.observed}")
print(f"report: {out / 'report.html'}")

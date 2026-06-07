"""Generate a full Plumb HTML report with execution-backed evidence.

Uses a routed fake session so the demo runs without a live Snowflake
account. It shows what an analyst sees on a real run: a grain fan-out
caught as BLOCKER with the duplicated key and a PII-redacted sample, a
reconciliation drift, plus passing static checks and honest coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plumb.config.models import CheckSpec, Ruleset  # noqa: E402
from plumb.engine.models import Target  # noqa: E402
from plumb.engine.runner import RunRequest, run_checks  # noqa: E402
from plumb.report.html import write_html  # noqa: E402
from plumb.report.json_out import write_json  # noqa: E402
from tests._fakes import RouteSession  # noqa: E402

SQL = """\
SELECT o.order_id, c.region, o.amount, o.created_at
FROM ANALYTICS.MART.ORDERS o
JOIN ANALYTICS.MART.DIM_CUSTOMER c ON o.cust_id = c.id
"""

ruleset = Ruleset(
    version="2026.06.0",
    certified_sources=["ANALYTICS.MART.FCT_SALES"],
    checks=[
        CheckSpec(id="S-STAT-001", enabled=True),
        CheckSpec(id="S-STAT-002", enabled=True),
        CheckSpec(id="S-STAT-008", enabled=True),
        CheckSpec(id="D-GRAIN-001", enabled=True, params={"key": ["order_id"]}),
        CheckSpec(id="D-DUP-001", enabled=True),
        CheckSpec(
            id="D-RECON-001",
            enabled=True,
            params={
                "metric_sql": "SELECT SUM(amount) AS m FROM {{ target }}",
                "source_of_truth_sql": "SELECT SUM(net_amount) AS m FROM ANALYTICS.MART.FCT_SALES",
                "tolerance_abs": 0,
                "tolerance_pct": 0.001,
            },
        ),
        CheckSpec(
            id="D-FRESH-001",
            enabled=True,
            params={"event_ts_col": "created_at", "sla_hours": 6},
        ),
        CheckSpec(id="R-DIFF-001", enabled=True),
    ],
)

# A grain fan-out: the join to dim_customer multiplies orders. The dup
# groups carry a PII column so redaction is visible in the report.
session = RouteSession()
session.add(
    "__PLUMB_DUP_COUNT",
    [
        {"ORDER_ID": 10231, "CUSTOMER_EMAIL": "ann@acme.com", "__PLUMB_DUP_COUNT": 4},
        {"ORDER_ID": 10244, "CUSTOMER_EMAIL": "bo@acme.com", "__PLUMB_DUP_COUNT": 3},
        {"ORDER_ID": 10250, "CUSTOMER_EMAIL": "cy@acme.com", "__PLUMB_DUP_COUNT": 2},
    ],
)
session.add("__PLUMB_DUP_ROWS", [{"__PLUMB_DUP_ROWS": 0}])
session.add("SUM(amount)", [{"M": 1042150.0}])   # the build total (inflated by fan-out)
session.add("FCT_SALES", [{"M": 312880.0}])      # the source of truth
import datetime as _dt  # noqa: E402

session.add(
    "__PLUMB_MAX_TS",
    [{"__PLUMB_MAX_TS": _dt.datetime(2026, 6, 7, 9, tzinfo=_dt.timezone.utc),
      "__PLUMB_NOW": _dt.datetime(2026, 6, 7, 12, tzinfo=_dt.timezone.utc)}],
)

result = run_checks(
    RunRequest(
        target=Target(type="sql", name="rpt_daily_sales", source_ref="queries/rpt_daily_sales.sql"),
        ruleset=ruleset,
        sql_text=SQL,
        profile="finance",
        session=session,
        run_id="demo-0c9aa207",
    )
)

out = Path("C:/Users/test/AppData/Local/Temp/demo_report")
out.mkdir(parents=True, exist_ok=True)
write_html(result, out / "report.html")
write_json(result, out / "report.json")
print(f"verdict: {result.verdict.value}")
print(f"report: {out / 'report.html'}")

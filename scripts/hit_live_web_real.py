"""Hit the running web server with a LIVE (static_only=false) check against
the real Snowflake view using the customer_ltv check set, exactly as the
browser does by default. Proves the web UI reaches real data."""

import json
import urllib.request

BASE = "http://127.0.0.1:8777"
SQL = (
    "SELECT customer_id, segment, region, lifetime_revenue, last_order_date "
    "FROM PORTFOLIO_DEMO_DB.ANALYTICS.V_CUSTOMER_LTV"
)

req = urllib.request.Request(
    BASE + "/api/check/sql",
    data=json.dumps(
        {"sql": SQL, "static_only": False, "rules": "customer_ltv"}
    ).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
result = json.loads(urllib.request.urlopen(req, timeout=90).read())
print("verdict:", result["verdict"])
env = result["environment"]
print("query_tag:", env["query_tag"], "| warehouse:", env["warehouse"])
for c in result["checks"]:
    if c["status"] != "SKIP":
        print(f"  {c['status']:5} {c['id']:12} {c['observed']}")

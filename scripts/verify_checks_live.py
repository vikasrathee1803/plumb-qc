"""Verify the checks really query Snowflake and do not false-positive.

Run A configures checks to the known-clean ground truth of V_CUSTOMER_LTV
(every one should PASS; any FAIL is a false positive). Run B is a control
set deliberately mis-configured (every one should FAIL; a PASS would mean
the check does not actually discriminate). Then we read QUERY_HISTORY for
run A's tag to prove the exact SQL executed on Snowflake.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plumb.config.loader import load_connection_profile  # noqa: E402
from plumb.config.models import CheckSpec, Ruleset  # noqa: E402
from plumb.connect.snowflake import SnowflakeSession  # noqa: E402
from plumb.engine.models import Target  # noqa: E402
from plumb.engine.runner import RunRequest, run_checks  # noqa: E402

VIEW = "PORTFOLIO_DEMO_DB.ANALYTICS.V_CUSTOMER_LTV"
SQL = f"SELECT customer_id, segment, region, lifetime_revenue, last_order_date FROM {VIEW}"
REGIONS = ["AMERICA", "ASIA", "EUROPE", "MIDDLE EAST", "AFRICA"]

SHOULD_PASS = [
    CheckSpec(id="D-GRAIN-001", enabled=True, params={"key": ["customer_id"]}),
    CheckSpec(id="D-NULL-001", enabled=True, params={"key": ["customer_id"]}),
    CheckSpec(id="D-POS-001", enabled=True, params={"columns": ["lifetime_revenue"]}),
    CheckSpec(id="D-RANGE-001", enabled=True, params={"column": "lifetime_revenue", "min": 0}),
    CheckSpec(id="D-DOMAIN-001", enabled=True, params={"column": "region", "allowed": REGIONS}),
    CheckSpec(id="D-DISTINCT-001", enabled=True, params={"column": "region", "min": 5, "max": 5}),
    CheckSpec(id="D-DUP-001", enabled=True),
    CheckSpec(
        id="D-RECON-001", enabled=True,
        params={
            "metric_sql": "SELECT SUM(lifetime_revenue) AS m FROM {{ target }}",
            "source_of_truth_sql": f"SELECT SUM(lifetime_revenue) AS m FROM {VIEW}",
            "tolerance_abs": 0, "tolerance_pct": 0,
        },
    ),
]

CONTROL_FAIL = [
    CheckSpec(id="D-RANGE-001", enabled=True, params={"column": "lifetime_revenue", "max": 1_000_000}),
    CheckSpec(id="D-DOMAIN-001", enabled=True, params={"column": "region", "allowed": ["AMERICA", "ASIA"]}),
    CheckSpec(id="D-DISTINCT-001", enabled=True, params={"column": "region", "min": 1, "max": 2}),
]


def run_with_session(label: str, specs: list[CheckSpec], expect_pass: bool):
    profile = load_connection_profile()
    import uuid

    rid = str(uuid.uuid4())
    session = SnowflakeSession(profile, run_id=rid, statement_timeout_s=120, max_result_rows=100000).open()
    try:
        result = run_checks(
            RunRequest(
                target=Target(type="sql", name="verify", source_ref=VIEW),
                ruleset=Ruleset(version="verify", checks=specs),
                sql_text=SQL, session=session, run_id=rid,
            )
        )
    finally:
        session.close()
    print(f"\n===== {label} =====  (expect all {'PASS' if expect_pass else 'FAIL'})")
    wrong = 0
    for c in result.checks:
        ok = (c.status.value == "PASS") if expect_pass else (c.status.value == "FAIL")
        flag = "OK" if ok else "  <-- UNEXPECTED"
        if not ok:
            wrong += 1
        print(f"  [{c.status.value:4}] {c.id:14} {c.observed} {flag}")
    print(f"  result: {'clean' if wrong == 0 else str(wrong) + ' UNEXPECTED'}")
    return rid, wrong


def show_history(tag_run_id: str):
    profile = load_connection_profile()
    s = SnowflakeSession(profile, run_id="audit", statement_timeout_s=60, max_result_rows=200).open()
    tag = f"plumb_qc:{tag_run_id}"
    q = (
        "SELECT QUERY_TEXT, EXECUTION_STATUS FROM "
        "TABLE(PORTFOLIO_DEMO_DB.INFORMATION_SCHEMA.QUERY_HISTORY()) "
        f"WHERE QUERY_TAG = '{tag}' ORDER BY START_TIME"
    )
    rows = []
    for _ in range(6):
        rows = s.execute(q).rows
        if rows:
            break
        time.sleep(2)
    s.close()
    print(f"\n===== QUERY_HISTORY for {tag} =====")
    print(f"  queries Snowflake recorded for this run: {len(rows)}")
    for r in rows:
        text = " ".join(str(r["QUERY_TEXT"]).split())[:90]
        print(f"  [{r['EXECUTION_STATUS']}] {text}")


if __name__ == "__main__":
    rid_pass, wrong_pass = run_with_session("Run A: ground-truth clean", SHOULD_PASS, True)
    rid_fail, wrong_fail = run_with_session("Run B: deliberately wrong controls", CONTROL_FAIL, False)
    show_history(rid_pass)
    print("\n===== VERDICT =====")
    print(f"  false positives (clean data flagged): {wrong_pass}")
    print(f"  missed true positives (bad data passed): {wrong_fail}")

"""AC6 live verification: confirm Plumb's queries land in QUERY_HISTORY
with the plumb_qc tag, on the expected warehouse, via the read-only session."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plumb.config.loader import load_connection_profile  # noqa: E402
from plumb.connect.snowflake import SnowflakeSession  # noqa: E402

prof = load_connection_profile()
session = SnowflakeSession(prof, run_id="audit", statement_timeout_s=60, max_result_rows=50)
session.open()

history_sql = (
    "SELECT QUERY_TAG, WAREHOUSE_NAME, ROLE_NAME, EXECUTION_STATUS, "
    "ROUND(TOTAL_ELAPSED_TIME/1000.0, 2) AS SECS "
    "FROM TABLE(PORTFOLIO_DEMO_DB.INFORMATION_SCHEMA.QUERY_HISTORY()) "
    "WHERE QUERY_TAG LIKE 'plumb_qc:%' ORDER BY START_TIME DESC"
)
result = session.execute(history_sql)
print(f"plumb-tagged queries in history: {len(result.rows)}")
tags = {}
for row in result.rows:
    tags.setdefault(row["QUERY_TAG"], 0)
    tags[row["QUERY_TAG"]] += 1
for tag, count in list(tags.items())[:6]:
    print(f"  {tag}  x{count}")
if result.rows:
    sample = result.rows[0]
    print("most recent:")
    print(f"  warehouse: {sample['WAREHOUSE_NAME']}")
    print(f"  role:      {sample['ROLE_NAME']}")
    print(f"  status:    {sample['EXECUTION_STATUS']}")
    print(f"  seconds:   {sample['SECS']}")

# The read-only guard must refuse a write even under ACCOUNTADMIN, before
# any statement reaches Snowflake.
from plumb.connect.snowflake import ReadOnlyViolation  # noqa: E402

try:
    session.execute("CREATE TABLE plumb_should_never_exist (x INT)")
    print("GUARD FAILED: a write was not refused")
except ReadOnlyViolation:
    print("read-only guard: refused CREATE TABLE before sending (as required)")

session.close()

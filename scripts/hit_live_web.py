"""Hit the running plumb web server over real HTTP."""

import json
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8777"


def post_json(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


prof = json.loads(urllib.request.urlopen(BASE + "/api/profiles", timeout=5).read())
print("profiles:", prof["profiles"], "| ruleset", prof["ruleset_version"])

sql = post_json("/api/check/sql", {"sql": "SELECT a FROM t, u", "static_only": True})
print("SQL verdict:", sql["verdict"])
blocker = next(c for c in sql["checks"] if c["id"] == "S-STAT-002")
print("  S-STAT-002:", blocker["status"], "-", blocker["observed"])

# Tableau upload via multipart, hand-built.
twb = Path("tests/fixtures/tableau/sales_dashboard.twb").read_bytes()
boundary = "----plumbboundary"
body = (
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="workbook"; filename="sales_dashboard.twb"\r\n'
    "Content-Type: application/xml\r\n\r\n"
).encode() + twb + f"\r\n--{boundary}--\r\n".encode()
req = urllib.request.Request(
    BASE + "/api/check/tableau",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="POST",
)
tab = json.loads(urllib.request.urlopen(req, timeout=10).read())
print("Tableau verdict:", tab["verdict"])
t = next(c for c in tab["checks"] if c["id"] == "T-SRC-003")
print("  T-SRC-003:", t["status"], "-", t["observed"])

html = urllib.request.urlopen(BASE + f"/api/report/{sql['run_id']}.html", timeout=5).read().decode()
print("report html bytes:", len(html), "| self-contained:", "<link" not in html)

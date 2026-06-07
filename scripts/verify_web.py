"""Verify the web backend serves the built SPA and runs a real check."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from web.api.app import create_app  # noqa: E402

client = TestClient(create_app())

root = client.get("/")
print("root status:", root.status_code)
print("serves built SPA index:", 'id="root"' in root.text and "assets/index-" in root.text)
print("is placeholder:", "not built yet" in root.text)

health = client.get("/api/health").json()
print("health:", health)

run = client.post(
    "/api/check/sql", json={"sql": "SELECT a FROM t, u", "static_only": True}
).json()
print("sql verdict:", run["verdict"], "| checks:", len(run["checks"]))

report = client.get(f"/api/report/{run['run_id']}.html")
print("report html status:", report.status_code, "| has verdict:", "BLOCKED" in report.text)

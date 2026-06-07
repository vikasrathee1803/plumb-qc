"""Web backend: every endpoint returns the same RunResult contract the CLI
produces, computed by the same engine. No verdict logic is reimplemented."""

from pathlib import Path

from fastapi.testclient import TestClient

from web.api.app import create_app

client = TestClient(create_app())
TABLEAU_FIXTURE = Path(__file__).parent / "fixtures" / "tableau" / "sales_dashboard.twb"


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_profiles_lists_shipped_profiles():
    r = client.get("/api/profiles")
    assert r.status_code == 200
    body = r.json()
    assert body["ruleset_version"]
    assert "finance" in body["profiles"]
    assert "marketing" in body["profiles"]


def test_sql_static_only_cartesian_join_is_blocked():
    r = client.post(
        "/api/check/sql",
        json={"sql": "SELECT a FROM t, u", "static_only": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "BLOCKED"
    # the contract shape the CLI and report writers also consume
    assert "coverage" in body and "summary" in body and "checks" in body
    assert any(c["id"] == "S-STAT-002" and c["status"] == "FAIL" for c in body["checks"])


def test_sql_clean_query_is_ready():
    r = client.post(
        "/api/check/sql",
        json={"sql": "SELECT a, b FROM db.s.t WHERE a > 0", "static_only": True},
    )
    assert r.status_code == 200
    assert r.json()["verdict"] in ("READY", "READY_WITH_NOTES")


def test_empty_sql_is_400():
    r = client.post("/api/check/sql", json={"sql": "   ", "static_only": True})
    assert r.status_code == 400


def test_unknown_profile_is_400():
    r = client.post(
        "/api/check/sql",
        json={"sql": "SELECT 1", "static_only": True, "profile": "nope"},
    )
    assert r.status_code == 400


def test_tableau_upload_runs_catalog():
    with TABLEAU_FIXTURE.open("rb") as fh:
        r = client.post(
            "/api/check/tableau",
            files={"workbook": ("sales_dashboard.twb", fh, "application/xml")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["target"]["type"] == "tableau"
    assert any(c["id"] == "T-SRC-003" for c in body["checks"])
    assert body["verdict"] in ("BLOCKED", "REVIEW", "READY_WITH_NOTES", "READY")


def test_report_html_available_after_run():
    run = client.post(
        "/api/check/sql", json={"sql": "SELECT a FROM t, u", "static_only": True}
    ).json()
    r = client.get(f"/api/report/{run['run_id']}.html")
    assert r.status_code == 200
    assert "BLOCKED" in r.text
    assert "<html" in r.text.lower()


def test_report_html_unknown_run_is_404():
    assert client.get("/api/report/does-not-exist.html").status_code == 404


def test_root_serves_something():
    # Either the built SPA or the build-me placeholder; never a 500.
    r = client.get("/")
    assert r.status_code == 200

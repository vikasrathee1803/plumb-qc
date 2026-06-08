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


def test_rulesets_lists_check_sets():
    r = client.get("/api/rulesets")
    assert r.status_code == 200
    body = r.json()
    assert "plumb" in body["rulesets"]
    assert "customer_ltv" in body["rulesets"]


def test_about_endpoint_reports_live_engine_facts():
    r = client.get("/api/about")
    assert r.status_code == 200
    body = r.json()
    assert body["total_checks"] > 0
    assert body["verdict_tiers"] == ["BLOCKED", "REVIEW", "READY_WITH_NOTES", "READY"]
    fams = {f["family"] for f in body["families"]}
    assert "assertions" in fams and "tableau_static" in fams
    assert any("read-only" in inv.lower() for inv in body["invariants"])
    assert "configured" in body["connection"]
    # tech stack carries real installed versions, grouped by layer
    stack_names = {it["name"] for g in body["stack"] for it in g["items"]}
    assert "sqlglot" in stack_names and "pydantic" in stack_names and "FastAPI" in stack_names
    assert all(it["version"] for g in body["stack"] for it in g["items"])


def test_profile_diff_is_computed_from_yaml():
    r = client.get("/api/profile?name=finance")
    assert r.status_code == 200
    changes = r.json()["changes"]
    text = " ".join(changes)
    assert "READY_WITH_NOTES" in text  # finance fails at a stricter gate
    assert any("aggregate" in c.lower() or "sample" in c.lower() for c in changes)


def test_unknown_profile_diff_is_400():
    assert client.get("/api/profile?name=nope").status_code == 400


def test_history_records_runs_and_run_detail_round_trips():
    run = client.post(
        "/api/check/sql", json={"sql": "SELECT a FROM t, u", "static_only": True}
    ).json()
    hist = client.get("/api/history").json()["runs"]
    assert any(h["run_id"] == run["run_id"] and h["verdict"] == "BLOCKED" for h in hist)
    detail = client.get(f"/api/run/{run['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["run_id"] == run["run_id"]


def test_run_detail_unknown_is_404():
    assert client.get("/api/run/nope").status_code == 404


def test_connection_endpoint_reports_configuration():
    r = client.get("/api/connection")
    assert r.status_code == 200
    # Either configured (a profile exists) or not; never an error.
    assert "configured" in r.json()


def test_unknown_ruleset_is_400():
    r = client.post(
        "/api/check/sql",
        json={"sql": "SELECT 1", "static_only": True, "rules": "no_such_set"},
    )
    assert r.status_code == 400


def test_customer_ltv_check_set_enables_data_assertions():
    """The customer_ltv check set enables grain/recon, so a static-only run
    against it skips the execution checks but still loads the richer set."""
    r = client.post(
        "/api/check/sql",
        json={
            "sql": "SELECT customer_id FROM PORTFOLIO_DEMO_DB.ANALYTICS.V_CUSTOMER_LTV",
            "static_only": True,
            "rules": "customer_ltv",
        },
    )
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["checks"]]
    assert "D-GRAIN-001" in ids and "D-RECON-001" in ids


def test_checks_catalog_lists_registered_checks_with_params():
    r = client.get("/api/checks")
    assert r.status_code == 200
    checks = r.json()["checks"]
    by_id = {c["id"]: c for c in checks}
    assert "D-GRAIN-001" in by_id
    assert by_id["D-GRAIN-001"]["family"] == "assertions"
    # parametrized checks expose their param hints
    grain_params = {p["name"] for p in by_id["D-GRAIN-001"]["params"]}
    assert "key" in grain_params
    # the new checks are in the catalog
    assert "D-POS-001" in by_id and "S-STAT-011" in by_id


def test_configurable_checks_override_what_runs():
    """The UI can send an explicit check set; only those run."""
    r = client.post(
        "/api/check/sql",
        json={
            "sql": "SELECT a FROM t, u",
            "static_only": True,
            "checks": [
                {"id": "S-STAT-001", "enabled": True},
                {"id": "S-STAT-002", "enabled": False},
            ],
        },
    )
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["checks"]]
    assert "S-STAT-001" in ids
    assert "S-STAT-002" not in ids  # disabled by the client


def test_configurable_check_params_flow_through():
    r = client.post(
        "/api/check/sql",
        json={
            "sql": "SELECT order_id FROM t",
            "static_only": True,
            "checks": [{"id": "D-GRAIN-001", "enabled": True, "params": {"key": ["order_id"]}}],
        },
    )
    assert r.status_code == 200
    grain = next(c for c in r.json()["checks"] if c["id"] == "D-GRAIN-001")
    # static-only: no session, so it skips, but the check was selected and ran
    assert grain["status"] == "SKIP"


def test_custom_check_flows_through_the_api():
    """A user-authored custom assertion reaches the engine and gets a
    distinct D-CUSTOM id in the result."""
    r = client.post(
        "/api/check/sql",
        json={
            "sql": "SELECT id FROM t",
            "static_only": True,
            "checks": [
                {
                    "id": "D-CUSTOM-001",
                    "enabled": True,
                    "params": {
                        "name": "no negatives",
                        "sql": "SELECT * FROM {{ target }} WHERE x < 0",
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["checks"]]
    assert "D-CUSTOM:no negatives" in ids


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


def test_tableau_checks_override_disables_a_check():
    """The Tableau tab can disable specific T-* checks."""
    import json

    with TABLEAU_FIXTURE.open("rb") as fh:
        r = client.post(
            "/api/check/tableau",
            files={"workbook": ("sales_dashboard.twb", fh, "application/xml")},
            data={"checks": json.dumps([{"id": "T-SRC-001", "enabled": True, "params": {}}])},
        )
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["checks"]]
    assert ids == ["T-SRC-001"]  # only the one we enabled ran


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

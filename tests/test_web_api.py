"""Web backend: every endpoint returns the same RunResult contract the CLI
produces, computed by the same engine. No verdict logic is reimplemented."""

import os
from pathlib import Path

from fastapi.testclient import TestClient

from web.api.app import create_app

_TOKEN = os.environ["PLUMB_API_TOKEN"]
client = TestClient(create_app(), headers={"X-Plumb-Token": _TOKEN})
TABLEAU_FIXTURE = Path(__file__).parent / "fixtures" / "tableau" / "sales_dashboard.twb"


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_requires_token():
    """Data endpoints reject a missing or wrong token; health stays open."""
    noauth = TestClient(create_app())  # no token header
    assert noauth.get("/api/health").status_code == 200  # health is open
    assert noauth.get("/api/profiles").status_code == 401
    assert noauth.post("/api/check/sql", json={"sql": "SELECT 1"}).status_code == 401
    bad = TestClient(create_app(), headers={"X-Plumb-Token": "wrong"})
    assert bad.get("/api/profiles").status_code == 401


def test_spa_shell_sets_the_token_cookie():
    r = client.get("/")
    assert r.status_code == 200
    assert "plumb_token" in r.cookies or "set-cookie" in {k.lower() for k in r.headers}


def test_disable_auth_bypasses_token_for_local_dev(monkeypatch):
    """PLUMB_DISABLE_AUTH lets the Vite dev server reach the API without the
    cookie. The default (auth on) is unchanged."""
    monkeypatch.setenv("PLUMB_DISABLE_AUTH", "1")
    dev = TestClient(create_app())  # no token header
    assert dev.get("/api/profiles").status_code == 200
    assert dev.post("/api/lineage", json={"sql": "SELECT a FROM t"}).status_code == 200


def test_web_run_is_audited():
    import json

    run = client.post(
        "/api/check/sql", json={"sql": "SELECT a FROM audit_probe_tbl, u", "static_only": True}
    ).json()
    audit_path = Path(os.environ["PLUMB_AUDIT_FILE"])
    assert audit_path.exists()
    records = [json.loads(ln) for ln in audit_path.read_text().splitlines() if ln.strip()]
    mine = next(r for r in records if r["run_id"] == run["run_id"])
    assert mine["verdict"] == "BLOCKED"
    assert mine["target_name"] == "audit_probe_tbl"
    assert mine["user"] and "timestamp" in mine


def test_profiles_lists_shipped_profiles():
    r = client.get("/api/profiles")
    assert r.status_code == 200
    body = r.json()
    assert body["ruleset_version"]
    assert "finance" in body["profiles"]
    assert "marketing" in body["profiles"]


def test_lineage_endpoint_returns_graph():
    r = client.post("/api/lineage", json={"sql": "SELECT a FROM t, u"})
    assert r.status_code == 200
    g = r.json()
    kinds = {n["kind"] for n in g["nodes"]}
    assert "table" in kinds and "output" in kinds
    assert any(e["risk"] for e in g["edges"])  # the comma join is flagged
    assert g["risks"]


def test_lineage_unparseable_is_400():
    assert client.post("/api/lineage", json={"sql": "SELEKT )("}).status_code == 400


def test_check_sql_folds_multistatement_build():
    sql = (
        "USE WAREHOUSE WH;\n"
        "CREATE OR REPLACE TEMP TABLE stg AS SELECT id, amount FROM raw WHERE amount > 0;\n"
        "CREATE OR REPLACE TABLE daily AS "
        "SELECT id, SUM(amount) AS total FROM stg, regions GROUP BY id;"
    )
    r = client.post("/api/check/sql", json={"sql": sql, "static_only": True})
    assert r.status_code == 200
    j = r.json()
    assert j["target"]["name"] == "daily"  # the build target, not a source table
    assert j.get("build_notes") and "daily" in j["build_notes"][0]
    assert j["verdict"] == "BLOCKED"  # the comma join in the final build is caught


def test_lineage_folds_multistatement_build():
    sql = (
        "CREATE TEMP TABLE stg AS SELECT a FROM raw;\n"
        "CREATE TABLE final AS SELECT a FROM stg, other;"
    )
    r = client.post("/api/lineage", json={"sql": sql})
    assert r.status_code == 200
    body = r.json()
    assert body.get("build_notes")
    assert any(e["risk"] for e in body["edges"])  # comma-join fan-out still flagged


def test_check_sql_with_no_read_is_400():
    r = client.post("/api/check/sql", json={"sql": "USE WAREHOUSE WH;", "static_only": True})
    assert r.status_code == 400


def test_columns_endpoint_detects_and_suggests():
    sql = (
        "CREATE VIEW v AS SELECT account_id, SUM(revenue) AS revenue, "
        "MAX(updated_at) AS updated_at FROM t GROUP BY account_id"
    )
    body = client.post("/api/columns", json={"sql": sql}).json()
    assert set(body["columns"]) == {"account_id", "revenue", "updated_at"}
    assert "account_id" in body["suggestions"]["key"]
    assert "updated_at" in body["suggestions"]["timestamp"]
    assert "revenue" in body["suggestions"]["amount"]


def test_columns_endpoint_empty_for_unparseable():
    body = client.post("/api/columns", json={"sql": "SELEKT )("}).json()
    assert body["columns"] == []


def test_run_detail_survives_cache_eviction(monkeypatch):
    import web.api.app as appmod

    appmod._REPORTS.clear()
    monkeypatch.setattr(appmod, "_REPORTS_MEM_CAP", 1)
    ids = []
    for i in range(3):
        body = {"sql": f"SELECT {i} AS a FROM t", "static_only": True}
        r = client.post("/api/check/sql", json=body)
        assert r.status_code == 200
        ids.append(r.json()["run_id"])
    assert ids[0] not in appmod._REPORTS  # evicted from the in-memory cache (cap 1)
    detail = client.get(f"/api/run/{ids[0]}")  # still served, reloaded from disk
    assert detail.status_code == 200 and detail.json()["run_id"] == ids[0]


def test_run_detail_rejects_unsafe_id():
    assert client.get("/api/run/..%2F..%2Fsecret").status_code == 404


def test_tableau_upload_accepts_twbx_bundling_data():
    """A .twbx that bundles a data extract is checked from its .twb XML; the
    package size does not block it."""
    import io
    import zipfile

    twb = TABLEAU_FIXTURE.read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("wb.twb", twb)
        z.writestr("Data/extract.hyper", b"x" * (2 * 1024 * 1024))  # 2 MB of data
    buf.seek(0)
    r = client.post(
        "/api/check/tableau",
        files={"workbook": ("wb.twbx", buf, "application/octet-stream")},
    )
    assert r.status_code == 200
    assert r.json()["verdict"]


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


def test_history_limit_and_search():
    probe = {"sql": "SELECT a FROM hist_search_tbl, u", "static_only": True}
    for _ in range(4):
        client.post("/api/check/sql", json=probe)
    recent = client.get("/api/history?limit=3").json()
    assert len(recent["runs"]) == 3
    assert recent["total"] >= 4
    found = client.get("/api/history?limit=1000&q=hist_search_tbl").json()
    assert all(r["target"] == "hist_search_tbl" for r in found["runs"])
    assert found["matched"] >= 4
    # verdict search works too
    assert client.get("/api/history?q=BLOCKED").json()["matched"] >= 1


def test_trend_accumulates_per_target():
    # A unique table name makes a unique, isolatable build target.
    sql = "SELECT a FROM trend_probe_tbl, u"  # cartesian -> BLOCKED
    for _ in range(3):
        client.post("/api/check/sql", json={"sql": sql, "static_only": True})
    tr = client.get("/api/trend?target=trend_probe_tbl").json()
    assert tr["target"] == "trend_probe_tbl"
    assert len(tr["points"]) >= 3
    assert all(p["verdict"] == "BLOCKED" for p in tr["points"])
    assert tr["ready_or_better"] == 0
    # points are oldest to newest
    times = [p["timestamp"] for p in tr["points"]]
    assert times == sorted(times)


_SF_PAYLOAD = {
    "account": "acct.region.azure", "user": "ANALYST", "authenticator": "snowflake_jwt",
    "private_key_path": "/keys/plumb.p8", "role": "PLUMB_QC", "warehouse": "PLUMB_WH",
    "passphrase": "s3cr3t-pass",
}


def test_snowflake_settings_save_get_and_secret_never_leaks():
    assert client.post("/api/settings/snowflake", json=_SF_PAYLOAD).status_code == 200
    g = client.get("/api/settings/snowflake").json()
    assert g["configured"] and g["account"] == "acct.region.azure"
    assert g["authenticator"] == "snowflake_jwt" and g["role"] == "PLUMB_QC"
    assert g["has_passphrase"] is True  # stored, reported as a boolean
    # the secret value and key are never returned in the response
    assert "passphrase" not in g and "s3cr3t-pass" not in str(g)


def test_snowflake_settings_validation_error_is_400():
    bad = {k: v for k, v in _SF_PAYLOAD.items() if k != "private_key_path"}
    bad.pop("passphrase", None)
    r = client.post("/api/settings/snowflake", json=bad)  # jwt needs a key path
    assert r.status_code == 400
    assert "private_key_path" in r.json()["detail"]


def test_connection_profile_model_refuses_password():
    import pytest
    from pydantic import ValidationError

    from plumb.config.models import ConnectionProfile

    with pytest.raises(ValidationError):
        ConnectionProfile.model_validate({**_SF_PAYLOAD, "password": "nope"})


def test_snowflake_settings_pat_save_and_secret_never_leaks():
    payload = {
        "account": "acct.region.azure", "user": "ANALYST", "authenticator": "pat",
        "role": "PLUMB_QC", "warehouse": "PLUMB_WH", "pat": "secret-pat-token",
    }
    assert client.post("/api/settings/snowflake", json=payload).status_code == 200
    g = client.get("/api/settings/snowflake").json()
    assert g["configured"] and g["authenticator"] == "pat" and g["has_pat"] is True
    assert "secret-pat-token" not in str(g) and "pat" not in g


def test_snowflake_settings_delete():
    client.post("/api/settings/snowflake", json=_SF_PAYLOAD)
    assert client.delete("/api/settings/snowflake").status_code == 200
    assert client.get("/api/settings/snowflake").json()["configured"] is False


def test_tableau_settings_pat_save_get_secret_isolation():
    payload = {
        "server": "https://10ax.online.tableau.com", "site": "analytics",
        "auth": "pat", "pat_name": "plumb-token", "secret": "tok-value-123",
    }
    assert client.post("/api/settings/tableau", json=payload).status_code == 200
    g = client.get("/api/settings/tableau").json()
    assert g["configured"] and g["auth"] == "pat" and g["pat_name"] == "plumb-token"
    assert g["has_secret"] is True
    assert "secret" not in g and "tok-value-123" not in str(g)


def test_settings_endpoints_require_token():
    noauth = TestClient(create_app())
    assert noauth.get("/api/settings/snowflake").status_code == 401
    assert noauth.post("/api/settings/tableau", json={"server": "x"}).status_code == 401


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


def test_report_path_traversal_is_rejected():
    # A run id with traversal/path characters must never reach the filesystem.
    for bad in ("..%2f..%2fsecret", "a/b", "a.b", "a b"):
        r = client.get(f"/api/report/{bad}.html")
        assert r.status_code == 404


def test_oversized_sql_is_rejected():
    big = "SELECT a FROM t WHERE x IN (" + ",".join("1" for _ in range(60000)) + ")"
    assert len(big) > 100_000
    assert client.post("/api/check/sql", json={"sql": big, "static_only": True}).status_code == 400
    assert client.post("/api/lineage", json={"sql": big}).status_code == 400


def test_report_link_survives_a_restart():
    """A shared report link must not rot: a fresh app (simulating a restart,
    empty in-memory store) still serves the persisted HTML."""
    run = client.post(
        "/api/check/sql", json={"sql": "SELECT a FROM t, u", "static_only": True}
    ).json()
    fresh = TestClient(create_app(), headers={"X-Plumb-Token": _TOKEN})
    r = fresh.get(f"/api/report/{run['run_id']}.html")
    assert r.status_code == 200
    assert "BLOCKED" in r.text


def test_root_serves_something():
    # Either the built SPA or the build-me placeholder; never a 500.
    r = client.get("/")
    assert r.status_code == 200

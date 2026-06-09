"""FastAPI app: the web surface over the same engine the CLI uses.

Every endpoint calls plumb.engine.runner.run_checks and returns the
RunResult contract unchanged. No verdict logic lives here. The SPA renders
that contract, so the web verdict is identical to the CLI verdict by
construction. Static-only is the default so the UI works with no Snowflake
connection; set static_only false to use the configured connection.
"""

from __future__ import annotations

import os
import re
import secrets
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from plumb import __version__
from plumb.baseline.store import make_baseline_store
from plumb.checks._sql import SqlParseError
from plumb.checks._tableau import TableauParseError, parse_workbook
from plumb.config.loader import (
    CONNECTION_FILE,
    PLUMB_HOME,
    TABLEAU_FILE,
    ConfigError,
    load_baseline_store_config,
    load_connection_profile,
    load_profile,
    load_ruleset,
    load_tableau_connection,
    resolve_profile,
)
from plumb.config.models import CheckSpec, Ruleset
from plumb.config.settings import (
    delete_secret,
    get_secret,
    has_secret,
    oauth_entry,
    passphrase_entry,
    tableau_app_entry,
    tableau_pat_entry,
    write_snowflake,
    write_tableau,
)
from plumb.connect.snowflake import (
    AuthConfigError,
    SnowflakeConnectError,
    SnowflakeSession,
    is_privileged_role,
)
from plumb.engine.catalog import catalog as check_catalog
from plumb.engine.lineage import build_lineage
from plumb.engine.models import RunResult, Target
from plumb.engine.runner import RunRequest, run_checks
from plumb.report.html import render_html

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RULES = REPO_ROOT / "rules" / "plumb.yml"
PROFILES_DIR = REPO_ROOT / "rules" / "profiles"
SPA_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"

# Reports are held in memory by run_id so the SPA can request the
# self-contained HTML for the run it just executed. _HISTORY is the ordered
# index (most recent first) for the recent-runs view.
_REPORTS: dict[str, RunResult] = {}
_HISTORY: list[dict[str, Any]] = []  # most recent first
_HISTORY_MEM_CAP = 1000
# Reports and the run log are written here so shared links and trends
# survive a restart and accumulate over time. Overridable so tests never
# pollute a user's real history.
WEB_REPORTS_DIR = Path(os.environ.get("PLUMB_WEB_REPORTS_DIR") or (PLUMB_HOME / "reports" / "web"))
HISTORY_FILE = WEB_REPORTS_DIR / "history.jsonl"

# Run ids are uuids; reject anything else before it touches the filesystem
# (path traversal guard). SQL inputs are capped to keep the parser bounded.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_SQL_CHARS = 100_000
_MAX_WORKBOOK_BYTES = 25 * 1024 * 1024


def _history_entry(result: RunResult) -> dict[str, Any]:
    s = result.summary
    return {
        "run_id": result.run_id,
        "verdict": result.verdict.value,
        "target": result.target.name,
        "type": result.target.type,
        "timestamp": result.timestamp.isoformat(),
        "checks": len(result.checks),
        "passed": s.passed,
        "failed": s.blocker + s.high + s.medium + s.low,
    }


def _load_history() -> None:
    if not HISTORY_FILE.exists():
        return
    import json

    entries: list[dict[str, Any]] = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    _HISTORY[:] = list(reversed(entries))[:_HISTORY_MEM_CAP]


def _record(result: RunResult) -> None:
    import json

    _REPORTS[result.run_id] = result
    entry = _history_entry(result)
    try:
        WEB_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (WEB_REPORTS_DIR / f"{result.run_id}.html").write_text(
            render_html(result), encoding="utf-8"
        )
        with HISTORY_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        # Audit every web run (who, when, target, ruleset version, verdict).
        from plumb.engine.audit import write_audit_record

        write_audit_record(result)
    except OSError:
        pass  # an unwritable reports/audit dir must never break a run
    _HISTORY.insert(0, entry)
    del _HISTORY[_HISTORY_MEM_CAP:]


class CheckConfig(BaseModel):
    id: str
    enabled: bool = True
    params: dict[str, Any] = {}


class LineageRequest(BaseModel):
    sql: str


class SnowflakeSettings(BaseModel):
    account: str
    user: str
    authenticator: str  # snowflake_jwt | externalbrowser | oauth
    private_key_path: str | None = None
    role: str
    warehouse: str
    passphrase: str | None = None  # key passphrase -> keychain (None leaves as-is)
    oauth_token: str | None = None  # -> keychain


class TableauSettings(BaseModel):
    server: str
    site: str = ""
    auth: str = "pat"  # pat | connected_app
    pat_name: str | None = None
    client_id: str | None = None
    secret_id: str | None = None
    username: str | None = None
    secret: str | None = None  # token value / app secret -> keychain


class SqlCheckRequest(BaseModel):
    sql: str
    profile: str | None = None
    rules: str | None = None
    static_only: bool = True
    baseline: str | None = None
    explain: bool = False
    checks: list[CheckConfig] | None = None


def _ruleset_path(name: str | None) -> Path:
    if not name or name == "plumb":
        return DEFAULT_RULES
    candidate = REPO_ROOT / "rules" / f"{name}.yml"
    if not candidate.exists():
        raise HTTPException(status_code=400, detail=f"unknown check set: {name}")
    return candidate


def _resolve_ruleset(profile: str | None, rules: str | None = None) -> Ruleset:
    ruleset = load_ruleset(_ruleset_path(rules), enforce_pin=False)
    if profile:
        profile_path = PROFILES_DIR / f"{profile}.yml"
        if not profile_path.exists():
            raise HTTPException(status_code=400, detail=f"unknown profile: {profile}")
        ruleset = resolve_profile(ruleset, load_profile(profile_path))
    return ruleset


def _open_session(ruleset: Ruleset, run_id: str) -> SnowflakeSession:
    try:
        connection = load_connection_profile()
        return SnowflakeSession(
            connection,
            run_id=run_id,
            statement_timeout_s=ruleset.defaults.statement_timeout_s,
            max_result_rows=ruleset.defaults.max_result_rows,
        ).open()
    except (ConfigError, AuthConfigError, SnowflakeConnectError) as exc:
        raise HTTPException(
            status_code=503, detail=f"Snowflake connection unavailable: {exc}"
        ) from exc


def _pkg_version(name: str) -> str | None:
    import importlib.metadata as meta

    try:
        return meta.version(name)
    except meta.PackageNotFoundError:
        return None


def _frontend_versions() -> dict[str, str]:
    import json

    pkg = REPO_ROOT / "web" / "ui" / "package.json"
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {**data.get("dependencies", {}), **data.get("devDependencies", {})}


def _build_stack() -> list[dict[str, Any]]:
    import sys

    fe = _frontend_versions()
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    groups: list[tuple[str, list[tuple[str, str | None]]]] = [
        ("Language", [("Python", py), ("TypeScript", fe.get("typescript"))]),
        ("SQL parsing & lint", [
            ("sqlglot", _pkg_version("sqlglot")),
            ("sqlfluff", _pkg_version("sqlfluff")),
        ]),
        ("Snowflake & Tableau", [
            ("snowflake-connector-python", _pkg_version("snowflake-connector-python")),
            ("tableauserverclient", _pkg_version("tableauserverclient")),
            ("lxml", _pkg_version("lxml")),
        ]),
        ("Contracts & config", [
            ("pydantic", _pkg_version("pydantic")),
            ("PyYAML", _pkg_version("PyYAML")),
        ]),
        ("CLI", [("typer", _pkg_version("typer")), ("rich", _pkg_version("rich"))]),
        ("Reporting & data", [
            ("Jinja2", _pkg_version("Jinja2")),
            ("pyarrow", _pkg_version("pyarrow")),
        ]),
        ("Web", [
            ("FastAPI", _pkg_version("fastapi")),
            ("uvicorn", _pkg_version("uvicorn")),
            ("React", fe.get("react")),
            ("Vite", fe.get("vite")),
        ]),
        ("AI assist", [
            ("Snowflake Cortex", "in-database"),
        ]),
        ("Quality gates", [
            ("pytest", _pkg_version("pytest")),
            ("ruff", _pkg_version("ruff")),
            ("mypy", _pkg_version("mypy")),
        ]),
    ]
    out: list[dict[str, Any]] = []
    for label, items in groups:
        present = [{"name": n, "version": v} for n, v in items if v]
        if present:
            out.append({"group": label, "items": present})
    return out


def _profile_changes(base: Ruleset, resolved: Ruleset) -> list[str]:
    """Human-readable diff of a resolved profile vs the base ruleset. Read
    from the actual config so the UI never overstates what a standard does."""
    changes: list[str] = []
    bd, rd = base.defaults, resolved.defaults
    if rd.fail_on != bd.fail_on:
        changes.append(f"Fails CI at {rd.fail_on} (base {bd.fail_on})")
    if rd.aggregate_only and not bd.aggregate_only:
        changes.append("Suppresses all row samples (aggregate only)")
    if rd.evidence_sample_rows != bd.evidence_sample_rows:
        changes.append(
            f"Evidence sample rows: {rd.evidence_sample_rows} (base {bd.evidence_sample_rows})"
        )
    if rd.redact_pii != bd.redact_pii:
        changes.append(f"PII redaction: {'on' if rd.redact_pii else 'off'}")
    if rd.statement_timeout_s != bd.statement_timeout_s:
        changes.append(
            f"Statement timeout: {rd.statement_timeout_s}s (base {bd.statement_timeout_s}s)"
        )
    if rd.max_result_rows != bd.max_result_rows:
        changes.append(f"Row cap: {rd.max_result_rows} (base {bd.max_result_rows})")
    base_null = base.thresholds.null_rate_default
    res_null = resolved.thresholds.null_rate_default
    if res_null != base_null:
        changes.append(f"Null-rate threshold: {res_null} (base {base_null})")
    base_fresh = base.thresholds.freshness_sla_hours_default
    res_fresh = resolved.thresholds.freshness_sla_hours_default
    if res_fresh != base_fresh:
        changes.append(f"Freshness SLA: {res_fresh}h (base {base_fresh}h)")
    for cid, sev in resolved.severity_overrides.items():
        if base.severity_overrides.get(cid) != sev:
            changes.append(f"{cid} severity raised to {sev.value}")
    if not changes:
        changes.append("The team default. Balanced gate, standard thresholds.")
    return changes


def _first_error(exc: ValidationError) -> str:
    """The first pydantic error as a readable 'field: message' string."""
    errors = exc.errors()
    if not errors:
        return "invalid settings"
    err = errors[0]
    loc = ".".join(str(p) for p in err.get("loc", ())) or "settings"
    return f"{loc}: {err.get('msg', 'invalid')}"


def _sql_target_name(sql: str) -> str:
    """A friendly name for a SQL build: its primary source table, else a
    generic label. Used in the verdict and the recent-runs strip."""
    try:
        from plumb.checks._sql import extract_table_refs

        refs = extract_table_refs(sql)
        if refs:
            return refs[0].name
    except Exception:  # noqa: BLE001
        pass
    return "SQL build"


def _maybe_explain(
    result: RunResult, sql_text: str | None, enabled: bool, session: Any = None
) -> None:
    if not enabled:
        return
    from plumb.ai import attach_explanations, get_client

    # Cortex assist runs in-database, so it needs the live session.
    client = get_client(session=session)
    if client is not None:
        attach_explanations(result, client, sql_text)


def create_app() -> FastAPI:
    app = FastAPI(title="Plumb", version=__version__)

    # Per-launch bearer token. The browser receives it as a SameSite=Strict,
    # HttpOnly cookie on the SPA shell and sends it automatically on same-origin
    # fetches; programmatic clients send the X-Plumb-Token header. This stops
    # another user on a shared host (or a malicious web page) from driving the
    # local API, and the SameSite cookie blocks CSRF. Override in automation
    # with PLUMB_API_TOKEN.
    api_token = os.environ.get("PLUMB_API_TOKEN") or secrets.token_urlsafe(24)
    app.state.api_token = api_token
    # Local-dev escape hatch: PLUMB_DISABLE_AUTH skips the token so the Vite dev
    # server (which serves its own SPA shell, so the cookie is never set) can
    # talk to the API. Loopback dev only; never set it for a shared deployment.
    disable_auth = os.environ.get("PLUMB_DISABLE_AUTH", "").lower() in ("1", "true", "yes")
    app.state.auth_disabled = disable_auth
    if disable_auth:
        import logging

        logging.getLogger("uvicorn.error").warning(
            "PLUMB_DISABLE_AUTH is set: the API token is NOT enforced. "
            "Use this only for local development on 127.0.0.1."
        )
    _OPEN_PATHS = {"/api/health"}

    @app.middleware("http")
    async def _require_token(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if not disable_auth and path.startswith("/api/") and path not in _OPEN_PATHS:
            presented = request.headers.get("X-Plumb-Token") or request.cookies.get("plumb_token")
            if not presented or not secrets.compare_digest(presented, api_token):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        response = await call_next(request)
        # Hand the token to the browser when it loads the SPA shell.
        if not disable_auth and (
            path == "/" or response.headers.get("content-type", "").startswith("text/html")
        ):
            response.set_cookie(
                "plumb_token", api_token, httponly=True, samesite="strict", path="/"
            )
        return response

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/profiles")
    def profiles() -> dict[str, Any]:
        ruleset = load_ruleset(DEFAULT_RULES, enforce_pin=False)
        names = sorted(p.stem for p in PROFILES_DIR.glob("*.yml")) if PROFILES_DIR.exists() else []
        return {"ruleset_version": ruleset.version, "profiles": names}

    @app.get("/api/rulesets")
    def rulesets() -> dict[str, Any]:
        rules_dir = REPO_ROOT / "rules"
        names = sorted(p.stem for p in rules_dir.glob("*.yml")) if rules_dir.exists() else []
        return {"default": "plumb", "rulesets": names}

    @app.post("/api/lineage")
    def lineage(req: LineageRequest) -> dict[str, Any]:
        """The relation-level lineage graph for a SQL build: source tables
        and views into CTEs and joins into the result, with fan-out risk."""
        if not req.sql.strip():
            raise HTTPException(status_code=400, detail="sql is required")
        if len(req.sql) > _MAX_SQL_CHARS:
            raise HTTPException(status_code=400, detail="SQL is too large to map")
        try:
            graph = build_lineage(req.sql)
        except SqlParseError as exc:
            raise HTTPException(status_code=400, detail=f"could not parse SQL: {exc}") from exc
        return graph.model_dump(mode="json")

    @app.get("/api/checks")
    def checks() -> dict[str, Any]:
        """The full catalog of registered checks with UI metadata. Lets the
        client render configurable toggles and per-check inputs."""
        return {"checks": check_catalog()}

    @app.get("/api/ruleset")
    def ruleset_detail(name: str = "plumb") -> dict[str, Any]:
        """A ruleset's configured check specs, to seed the config panel."""
        rs = load_ruleset(_ruleset_path(name), enforce_pin=False)
        return {
            "version": rs.version,
            "checks": [
                {"id": c.id, "enabled": c.enabled, "params": c.params} for c in rs.checks
            ],
        }

    @app.get("/api/about")
    def about() -> dict[str, Any]:
        """Live engine facts for the 'how it works' view: real counts from
        the registry, the connection, and the AI provider order, so the
        diagram reflects the running system rather than a static picture."""
        cat = check_catalog()
        fam: dict[str, int] = {}
        for c in cat:
            fam[c["family"]] = fam.get(c["family"], 0) + 1
        try:
            conn = load_connection_profile()
            connected = {"configured": True, "account": conn.account, "warehouse": conn.warehouse}
        except ConfigError:
            connected = {"configured": False}
        ai_ready = False
        try:
            from plumb.ai import cortex_enabled

            # Cortex assist is available when it is enabled and a connection
            # exists (it runs in-database on a live session).
            ai_ready = cortex_enabled() and bool(connected["configured"])
        except Exception:  # noqa: BLE001
            ai_ready = False
        return {
            "version": __version__,
            "total_checks": len(cat),
            "families": [{"family": k, "count": v} for k, v in sorted(fam.items())],
            "connection": connected,
            "ai_ready": ai_ready,
            "stack": _build_stack(),
            "verdict_tiers": ["BLOCKED", "REVIEW", "READY_WITH_NOTES", "READY"],
            "invariants": [
                "Read-only: the engine refuses any statement that is not a read",
                "Deterministic verdict: no LLM ever sets a status",
                "Every query is tagged plumb_qc:{run_id} on a dedicated warehouse",
                "Evidence samples are capped and PII-redacted",
            ],
        }

    @app.get("/api/connection")
    def connection() -> dict[str, Any]:
        """Report whether a live Snowflake connection is configured, so the
        UI can default to a live run. Does not connect (kept fast)."""
        try:
            profile = load_connection_profile()
        except ConfigError:
            return {"configured": False}
        return {
            "configured": True,
            "account": profile.account,
            "warehouse": profile.warehouse,
            "role": profile.role,
            "user": profile.user,
            "privileged_role": is_privileged_role(profile.role),
        }

    # ---- Setup / connection settings (credentials stay local: config in
    # ~/.plumb, secrets in the OS keychain; never returned in a response) ----

    @app.get("/api/settings/snowflake")
    def get_snowflake_settings() -> dict[str, Any]:
        try:
            p = load_connection_profile()
        except ConfigError:
            return {"configured": False}
        return {
            "configured": True,
            "account": p.account, "user": p.user, "authenticator": p.authenticator,
            "private_key_path": p.private_key_path, "role": p.role, "warehouse": p.warehouse,
            "privileged_role": is_privileged_role(p.role),
            "has_passphrase": has_secret(passphrase_entry(p.account, p.user)),
            "has_oauth_token": has_secret(oauth_entry(p.account, p.user)),
        }

    @app.post("/api/settings/snowflake")
    def save_snowflake_settings(req: SnowflakeSettings) -> dict[str, Any]:
        data = {
            "account": req.account, "user": req.user, "authenticator": req.authenticator,
            "private_key_path": req.private_key_path, "role": req.role, "warehouse": req.warehouse,
        }
        try:
            write_snowflake(data, passphrase=req.passphrase, oauth_token=req.oauth_token)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=_first_error(exc)) from exc
        return {"ok": True}

    @app.post("/api/settings/snowflake/test")
    def test_snowflake_connection() -> dict[str, Any]:
        try:
            prof = load_connection_profile()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail="save the connection first") from exc
        try:
            session = SnowflakeSession(
                prof, run_id=str(uuid.uuid4()), statement_timeout_s=30
            ).open()
            try:
                r = session.execute("SELECT CURRENT_ROLE() AS role, CURRENT_WAREHOUSE() AS wh")
            finally:
                session.close()
            row = r.rows[0] if r.rows else {}
            return {"ok": True, "role": row.get("ROLE"), "warehouse": row.get("WH")}
        except (AuthConfigError, SnowflakeConnectError, ConfigError) as exc:
            return {"ok": False, "error": str(exc)[:300]}

    @app.delete("/api/settings/snowflake")
    def delete_snowflake_settings() -> dict[str, Any]:
        try:
            p = load_connection_profile()
            delete_secret(passphrase_entry(p.account, p.user))
            delete_secret(oauth_entry(p.account, p.user))
        except ConfigError:
            pass
        if CONNECTION_FILE.exists():
            CONNECTION_FILE.unlink()
        return {"ok": True}

    @app.get("/api/settings/tableau")
    def get_tableau_settings() -> dict[str, Any]:
        try:
            c = load_tableau_connection()
        except ConfigError:
            return {"configured": False}
        secret_set = (
            has_secret(tableau_pat_entry(c.server, c.pat_name)) if c.auth == "pat" and c.pat_name
            else has_secret(tableau_app_entry(c.server, c.secret_id)) if c.secret_id else False
        )
        return {
            "configured": True, "server": c.server, "site": c.site, "auth": c.auth,
            "pat_name": c.pat_name, "client_id": c.client_id, "secret_id": c.secret_id,
            "username": c.username, "has_secret": secret_set,
        }

    @app.post("/api/settings/tableau")
    def save_tableau_settings(req: TableauSettings) -> dict[str, Any]:
        data = req.model_dump(exclude={"secret"})
        try:
            write_tableau(data, secret=req.secret)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=_first_error(exc)) from exc
        return {"ok": True}

    @app.post("/api/settings/tableau/test")
    def test_tableau_connection() -> dict[str, Any]:
        try:
            c = load_tableau_connection()
        except ConfigError as exc:
            raise HTTPException(
                status_code=400, detail="save the Tableau connection first"
            ) from exc
        if c.auth != "pat" or not c.pat_name:
            return {"ok": False, "error": "live test currently supports Personal Access Token auth"}
        secret = get_secret(tableau_pat_entry(c.server, c.pat_name))
        if not secret:
            return {"ok": False, "error": "no token secret stored; save it first"}
        try:
            import tableauserverclient as tsc

            auth = tsc.PersonalAccessTokenAuth(c.pat_name, secret, site_id=c.site)
            server = tsc.Server(c.server, use_server_version=True)
            with server.auth.sign_in(auth):
                return {"ok": True, "site": c.site or "default"}
        except Exception as exc:  # noqa: BLE001 - any Tableau error is a failed test
            return {"ok": False, "error": str(exc)[:300]}

    @app.delete("/api/settings/tableau")
    def delete_tableau_settings() -> dict[str, Any]:
        try:
            c = load_tableau_connection()
            if c.pat_name:
                delete_secret(tableau_pat_entry(c.server, c.pat_name))
            if c.secret_id:
                delete_secret(tableau_app_entry(c.server, c.secret_id))
        except ConfigError:
            pass
        if TABLEAU_FILE.exists():
            TABLEAU_FILE.unlink()
        return {"ok": True}

    @app.post("/api/check/sql")
    def check_sql(req: SqlCheckRequest) -> dict[str, Any]:
        if not req.sql.strip():
            raise HTTPException(status_code=400, detail="sql is required")
        if len(req.sql) > _MAX_SQL_CHARS:
            raise HTTPException(status_code=400, detail="SQL is too large")
        ruleset = _resolve_ruleset(req.profile, req.rules)
        if req.checks is not None:
            # The UI sent an explicit check configuration; it replaces the
            # ruleset's check list (defaults, naming, sources still apply).
            ruleset = ruleset.model_copy(
                update={
                    "checks": [
                        CheckSpec(id=c.id, enabled=c.enabled, params=c.params)
                        for c in req.checks
                    ]
                }
            )
        run_id = str(uuid.uuid4())
        session = None
        if not req.static_only:
            session = _open_session(ruleset, run_id)
        cfg = load_baseline_store_config()
        store = make_baseline_store(cfg.kind, Path(cfg.path) if cfg.path else None)
        try:
            result = run_checks(
                RunRequest(
                    target=Target(type="sql", name=_sql_target_name(req.sql), source_ref=None),
                    ruleset=ruleset,
                    sql_text=req.sql,
                    profile=req.profile,
                    session=session,
                    baseline_store=store,
                    baseline_name=req.baseline,
                    run_id=run_id,
                )
            )
            # Explain while the session is still open: Cortex runs in-database.
            _maybe_explain(result, req.sql, req.explain, session)
        finally:
            if session is not None:
                session.close()
        _record(result)
        return result.model_dump(mode="json")

    @app.post("/api/check/tableau")
    async def check_tableau(
        workbook: UploadFile = File(...),
        profile: str | None = Form(None),
        checks: str | None = Form(None),
    ) -> dict[str, Any]:
        ruleset = _resolve_ruleset(profile)
        if checks:
            import json

            try:
                parsed_checks = [CheckConfig.model_validate(c) for c in json.loads(checks)]
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=f"bad checks payload: {exc}") from exc
            ruleset = ruleset.model_copy(
                update={
                    "checks": [
                        CheckSpec(id=c.id, enabled=c.enabled, params=c.params)
                        for c in parsed_checks
                    ]
                }
            )
        suffix = Path(workbook.filename or "wb.twb").suffix or ".twb"
        data = await workbook.read()
        if len(data) > _MAX_WORKBOOK_BYTES:
            raise HTTPException(status_code=400, detail="workbook is too large")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            parsed = parse_workbook(tmp_path)
        except TableauParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            tmp_path.unlink(missing_ok=True)
        result = run_checks(
            RunRequest(
                target=Target(
                    type="tableau", name=workbook.filename or "workbook", source_ref=None
                ),
                ruleset=ruleset,
                workbook=parsed,
                profile=profile,
                run_id=str(uuid.uuid4()),
            )
        )
        _record(result)
        return result.model_dump(mode="json")

    @app.get("/api/history")
    def history(limit: int = 25, q: str | None = None) -> dict[str, Any]:
        """Runs, most recent first. limit caps the page; q filters by target
        or verdict (case-insensitive) for the full-history search."""
        runs = _HISTORY
        if q:
            needle = q.lower()
            runs = [
                r for r in runs
                if needle in r["target"].lower() or needle in r["verdict"].lower()
            ]
        return {"runs": runs[: max(0, limit)], "total": len(_HISTORY), "matched": len(runs)}

    @app.get("/api/trend")
    def trend(target: str) -> dict[str, Any]:
        """The verdict history for one build, oldest to newest, so the UI can
        show whether it has been getting better or worse."""
        for_target = [h for h in _HISTORY if h["target"] == target]
        points = list(reversed(for_target))[-20:]  # oldest to newest
        ready_or_better = sum(
            1 for p in points if p["verdict"] in ("READY", "READY_WITH_NOTES")
        )
        return {
            "target": target,
            "points": points,
            "total": len(for_target),
            "ready_or_better": ready_or_better,
        }

    @app.get("/api/run/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        result = _REPORTS.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="no run with that id")
        return result.model_dump(mode="json")

    @app.get("/api/profile")
    def profile_detail(name: str) -> dict[str, Any]:
        """What a standard (profile) actually changes vs the base ruleset,
        computed from the YAML so the UI tells the truth."""
        profile_path = PROFILES_DIR / f"{name}.yml"
        if not profile_path.exists():
            raise HTTPException(status_code=400, detail=f"unknown standard: {name}")
        base = load_ruleset(DEFAULT_RULES, enforce_pin=False)
        resolved = resolve_profile(base, load_profile(profile_path))
        return {"name": name, "changes": _profile_changes(base, resolved)}

    @app.get("/api/report/{run_id}.html", response_class=HTMLResponse)
    def report_html(run_id: str) -> HTMLResponse:
        if not _RUN_ID_RE.match(run_id):
            raise HTTPException(status_code=404, detail="no report for that run id")
        result = _REPORTS.get(run_id)
        if result is not None:
            return HTMLResponse(content=render_html(result))
        # Fall back to the persisted file so a shared link survives a restart.
        persisted = WEB_REPORTS_DIR / f"{run_id}.html"
        if persisted.exists():
            return HTMLResponse(content=persisted.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="no report for that run id")

    if SPA_DIST.exists():
        app.mount("/", StaticFiles(directory=str(SPA_DIST), html=True), name="spa")
    else:

        @app.get("/", response_class=HTMLResponse)
        def spa_missing() -> HTMLResponse:
            return HTMLResponse(
                "<h1>Plumb</h1><p>The web UI is not built yet. Run "
                "<code>npm install &amp;&amp; npm run build</code> in web/ui, "
                "then restart <code>plumb web</code>. The API is live at "
                "<code>/api/health</code>.</p>"
            )

    return app


_load_history()
app = create_app()

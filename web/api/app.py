"""FastAPI app: the web surface over the same engine the CLI uses.

Every endpoint calls plumb.engine.runner.run_checks and returns the
RunResult contract unchanged. No verdict logic lives here. The SPA renders
that contract, so the web verdict is identical to the CLI verdict by
construction. Static-only is the default so the UI works with no Snowflake
connection; set static_only false to use the configured connection.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from plumb import __version__
from plumb.baseline.store import make_baseline_store
from plumb.checks._tableau import TableauParseError, parse_workbook
from plumb.config.loader import (
    ConfigError,
    load_baseline_store_config,
    load_connection_profile,
    load_profile,
    load_ruleset,
    resolve_profile,
)
from plumb.config.models import CheckSpec, Ruleset
from plumb.connect.snowflake import (
    AuthConfigError,
    SnowflakeConnectError,
    SnowflakeSession,
)
from plumb.engine.catalog import catalog as check_catalog
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
_HISTORY: list[dict[str, Any]] = []
_HISTORY_CAP = 25


def _record(result: RunResult) -> None:
    _REPORTS[result.run_id] = result
    _HISTORY.insert(0, {
        "run_id": result.run_id,
        "verdict": result.verdict.value,
        "target": result.target.name,
        "type": result.target.type,
        "timestamp": result.timestamp.isoformat(),
        "checks": len(result.checks),
    })
    del _HISTORY[_HISTORY_CAP:]


class CheckConfig(BaseModel):
    id: str
    enabled: bool = True
    params: dict[str, Any] = {}


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
            ("openai (Groq)", _pkg_version("openai")),
            ("google-generativeai", _pkg_version("google-generativeai")),
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


def _maybe_explain(result: RunResult, sql_text: str | None, enabled: bool) -> None:
    if not enabled:
        return
    from plumb.ai import attach_explanations, get_client

    client = get_client()
    if client is not None:
        attach_explanations(result, client, sql_text)


def create_app() -> FastAPI:
    app = FastAPI(title="Plumb", version=__version__)

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
            from plumb.ai import get_client

            ai_ready = get_client() is not None
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
        }

    @app.post("/api/check/sql")
    def check_sql(req: SqlCheckRequest) -> dict[str, Any]:
        if not req.sql.strip():
            raise HTTPException(status_code=400, detail="sql is required")
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
        finally:
            if session is not None:
                session.close()
        _maybe_explain(result, req.sql, req.explain)
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
    def history() -> dict[str, Any]:
        """Recent runs, most recent first, for the confidence-over-time view."""
        return {"runs": _HISTORY}

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
        result = _REPORTS.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="no report for that run id")
        return HTMLResponse(content=render_html(result))

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


app = create_app()

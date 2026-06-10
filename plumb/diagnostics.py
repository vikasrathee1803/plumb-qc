"""Self-diagnostic: verify the environment can actually launch Plumb.

The feature tests drive objects through the FastAPI TestClient, so they never
exercise the real import graph of the launch path (uvicorn, the CLI `web`
command, the portable launcher). A missing runtime dependency therefore slips
past them and only surfaces as a cryptic `ModuleNotFoundError` at startup. This
module closes that gap: it imports every runtime dependency and every Plumb
module, then runs the engine and builds the web app end to end.

Run it the moment something will not start:

    plumb doctor                      # installed CLI
    python scripts/selfcheck.py       # from a source checkout
    check.bat                         # inside the portable build

It is dependency-light on purpose (no httpx/TestClient) so it runs in the
portable bundle, which ships only the runtime dependencies.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Callable

import plumb

# Import names (not distribution names) of every runtime dependency. These are
# what actually fail at launch when a wheel is missing from the interpreter.
RUNTIME_IMPORTS = [
    "sqlglot", "sqlfluff", "snowflake.connector", "pydantic", "yaml",
    "typer", "click", "rich", "jinja2", "pyarrow", "keyring", "lxml",
    "fastapi", "uvicorn", "multipart", "tableauserverclient",
]

# rules/ sits beside the plumb package in both the repo and the portable bundle.
_ROOT = Path(plumb.__file__).resolve().parent.parent
_RULES = _ROOT / "rules" / "plumb.yml"
_DIST = _ROOT / "web" / "ui" / "dist" / "index.html"

Check = tuple[str, bool, str]


def _dep(name: str) -> str:
    mod = importlib.import_module(name)
    return getattr(mod, "__version__", "ok")


def _ensure_web_importable() -> bool:
    """web/ is a repo-root sibling of the plumb package, not part of the
    wheel; the CLI `web` command inserts the repo root on sys.path before
    importing it. The doctor must judge the same launch path the `web`
    command actually uses — without this it false-FAILs a healthy install
    where plumb is importable but the repo root is not on sys.path."""
    if not (_ROOT / "web").is_dir():
        return False
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    return True


_WEB_ABSENT = "web/ not present (wheel install; the web UI needs a source checkout)"


def _all_modules_import() -> str:
    """Import every submodule of plumb (and web.api when present),
    surfacing the first break."""
    failures: list[str] = []
    count = 0
    packages = ["plumb"]
    if _ensure_web_importable():
        packages.append("web.api")
    for pkg_name in packages:
        pkg = importlib.import_module(pkg_name)
        names = [pkg_name] + [
            m.name for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + ".")
        ]
        for name in names:
            count += 1
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001 - report any import break
                failures.append(f"{name} ({type(exc).__name__}: {exc})")
    if failures:
        raise ImportError("; ".join(failures))
    return f"{count} modules"


def _registry_populated() -> str:
    import plumb.checks  # noqa: F401 - registers every check on import
    from plumb.engine.registry import all_checks

    n = len(all_checks())
    if n == 0:
        raise RuntimeError("no checks registered")
    return f"{n} checks"


def _engine_parses() -> str:
    from plumb.engine.lineage import build_lineage

    graph = build_lineage("SELECT a FROM t, u")
    return f"lineage: {len(graph.nodes)} nodes"


def _engine_runs() -> str:
    """A real static check, end to end, with no connection."""
    from plumb.baseline.store import make_baseline_store
    from plumb.config.loader import load_baseline_store_config, load_ruleset
    from plumb.engine.models import Target
    from plumb.engine.runner import RunRequest
    from plumb.engine.runner import run_checks as engine_run_checks

    ruleset = load_ruleset(_RULES, enforce_pin=False)
    cfg = load_baseline_store_config()
    store = make_baseline_store(cfg.kind, Path(cfg.path) if cfg.path else None)
    result = engine_run_checks(
        RunRequest(
            target=Target(type="sql", name="selfcheck", source_ref=None),
            ruleset=ruleset,
            sql_text="SELECT a FROM t, u",
            profile=None,
            session=None,
            baseline_store=store,
            baseline_name=None,
            run_id="selfcheck",
        )
    )
    if not result.verdict:
        raise RuntimeError("no verdict computed")
    return f"verdict: {result.verdict}"


def _web_app_builds() -> str:
    if not _ensure_web_importable():
        return _WEB_ABSENT
    from web.api.app import create_app

    create_app()
    return "create_app ok"


def _web_ui_built() -> str:
    if not (_ROOT / "web").is_dir():
        return _WEB_ABSENT
    if not _DIST.exists():
        raise FileNotFoundError(
            f"{_DIST} missing - run `npm run build` in web/ui (source checkout only)"
        )
    return "dist present"


def _python_version() -> str:
    if sys.version_info < (3, 11):
        raise RuntimeError(f"Python {sys.version.split()[0]} < 3.11")
    return sys.version.split()[0]


def diagnose() -> list[Check]:
    """Run every check and return (label, ok, detail) rows."""
    plan: list[tuple[str, Callable[[], str]]] = [
        ("Python >= 3.11", _python_version),
        *[(f"dependency: {name}", lambda n=name: _dep(n)) for name in RUNTIME_IMPORTS],
        ("all Plumb modules import", _all_modules_import),
        ("check registry populated", _registry_populated),
        ("engine parses SQL", _engine_parses),
        ("engine runs a static check", _engine_runs),
        ("web app constructs", _web_app_builds),
        ("web UI built", _web_ui_built),
    ]
    rows: list[Check] = []
    for label, fn in plan:
        try:
            rows.append((label, True, fn()))
        except Exception as exc:  # noqa: BLE001 - a failed check is the signal
            rows.append((label, False, f"{type(exc).__name__}: {exc}"))
    return rows


def main() -> int:
    """Print the report and return an exit code (0 ok, 1 if anything failed)."""
    rows = diagnose()
    print("Plumb self-check\n")
    for label, ok, detail in rows:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}" + (f"  ({detail})" if detail else ""))
    failed = [r for r in rows if not r[1]]
    print()
    if failed:
        print(f"{len(failed)} check(s) FAILED. Fix the items above, then re-run.")
        return 1
    print("All checks passed. Plumb is ready to launch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

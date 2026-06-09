"""Build a self-contained, portable Plumb for Windows.

Produces a zip that runs with no Python, no pip, and no installation: it bundles
an embeddable CPython, every runtime dependency, the built web UI, and a
double-click launcher. Unzip anywhere and run run.bat.

    python scripts/build_portable.py

Requires: an internet connection (to fetch the embeddable interpreter and the
dependency wheels) and a built web UI (web/ui/dist). Run `npm run build` in
web/ui first if dist is missing.
"""

import datetime
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from email.parser import Parser
from pathlib import Path

PY_VERSION = "3.12.4"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
PTH_NAME = "python312._pth"

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "portable"
BUNDLE = BUILD / "Plumb"
CACHE = ROOT / "build" / "_cache"
OUT_ZIP = ROOT / "dist_portable" / "Plumb-Portable-Windows-x64.zip"

# Runtime dependencies (the dev tools are left out). AI assist runs in-database
# via Snowflake Cortex through the connector below, so there is no LLM SDK.
RUNTIME_DEPS = [
    "sqlglot==26.3.8",
    "sqlfluff==4.2.2",
    "snowflake-connector-python==4.6.0",
    "pydantic==2.10.6",
    "PyYAML==6.0.2",
    "typer==0.15.1",
    "click==8.1.8",
    "rich==13.9.4",
    "jinja2==3.1.6",
    "pyarrow==24.0.0",
    "keyring==25.6.0",
    "lxml==6.1.1",
    "fastapi==0.136.3",
    "uvicorn==0.34.0",
    "python-multipart==0.0.32",
    "tableauserverclient==0.36",
]

APP_TREES = ["plumb", "web/api", "rules"]
RUN_PLUMB = '''"""Portable launcher for Plumb. Started by run.bat."""
import os
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "app"))


def _writable_data_root():
    """A data dir we can write to with no admin rights: the portable folder if
    it is writable, otherwise a per-user location (the LOCALAPPDATA folder, or
    the home directory). Plumb never writes to a system path."""
    candidate = os.path.join(HERE, "data")
    try:
        os.makedirs(candidate, exist_ok=True)
        probe = os.path.join(candidate, ".write_test")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        return candidate
    except OSError:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        root = os.path.join(base, "Plumb")
        os.makedirs(root, exist_ok=True)
        return root


_DATA = _writable_data_root()
os.environ.setdefault("PLUMB_WEB_REPORTS_DIR", os.path.join(_DATA, "reports", "web"))
os.environ.setdefault("PLUMB_AUDIT_FILE", os.path.join(_DATA, "audit.jsonl"))

HOST, PORT = "127.0.0.1", 8777


def _open_browser() -> None:
    time.sleep(1.8)
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    try:
        import uvicorn
        import web.api.app as appmod
    except ModuleNotFoundError as exc:
        print(f"Could not start Plumb: {exc}.")
        print("This usually means run_plumb.py was launched with the wrong Python.")
        print("Double-click run.bat instead - it uses this folder's bundled Python.")
        print("To diagnose, run check.bat.")
        sys.exit(1)

    token = getattr(appmod.app.state, "api_token", "")
    print(f"Plumb is running at http://{HOST}:{PORT}/")
    print(f"API token (sent to your browser automatically; use it for scripts): {token}")
    print("Close this window to stop it.")
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(appmod.app, host=HOST, port=PORT, log_level="warning")
'''

RUN_BAT = (
    "@echo off\r\n"
    "cd /d \"%~dp0\"\r\n"
    "echo Starting Plumb...\r\n"
    "\"%~dp0python\\python.exe\" \"%~dp0run_plumb.py\"\r\n"
    "pause\r\n"
)

CHECK_PLUMB = '''"""Portable self-check for Plumb. Started by check.bat."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "app"))

from plumb.diagnostics import main

raise SystemExit(main())
'''

CHECK_BAT = (
    "@echo off\r\n"
    "cd /d \"%~dp0\"\r\n"
    "\"%~dp0python\\python.exe\" \"%~dp0check_plumb.py\"\r\n"
    "pause\r\n"
)

README = (
    "Plumb - portable build\r\n"
    "======================\r\n\r\n"
    "Double-click run.bat. Your browser opens at http://127.0.0.1:8777/.\r\n"
    "Close the console window to stop.\r\n\r\n"
    "No administrator rights needed, to install or to run:\r\n"
    "  - Nothing is installed. This folder carries its own Python and every\r\n"
    "    dependency. Unzip it anywhere you can write (Desktop, Downloads, a\r\n"
    "    home or network folder) and run it.\r\n"
    "  - It listens only on 127.0.0.1 (loopback) on port 8777, so there is no\r\n"
    "    firewall prompt and nothing is exposed to the network.\r\n"
    "  - It writes only to this folder's data\\ directory, or to your user\r\n"
    "    profile if this folder is read-only. It never touches Program Files,\r\n"
    "    the registry, or any system path.\r\n\r\n"
    "Live Snowflake checks read ~/.plumb/connection.yml (key-pair / SSO).\r\n"
    "Without it, Plumb runs every static check and the query map offline.\r\n\r\n"
    "Troubleshooting:\r\n"
    "  - Always start with run.bat, not run_plumb.py. run.bat uses the Python\r\n"
    "    bundled in this folder; double-clicking run_plumb.py may use a different\r\n"
    "    Python that is missing the dependencies (e.g. 'No module named uvicorn').\r\n"
    "  - If it will not start, run check.bat. It reports every dependency and\r\n"
    "    component as PASS or FAIL so you can see exactly what is wrong.\r\n"
)


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def write_sbom(site_packages: Path, dest: Path) -> int:
    """Emit a CycloneDX SBOM of every bundled package, read from the installed
    dist-info, so infosec can inventory and CVE-scan the portable build."""
    components = []
    for meta in sorted(site_packages.glob("*.dist-info/METADATA")):
        fields = Parser().parsestr(meta.read_text(encoding="utf-8", errors="replace"))
        name, version = fields.get("Name"), fields.get("Version")
        if name and version:
            components.append({
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name}@{version}",
            })
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "component": {"type": "application", "name": "plumb", "version": "0.1.0"},
        },
        "components": components,
    }
    dest.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    return len(components)


def fetch_embeddable() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"python-{PY_VERSION}-embed-amd64.zip"
    if dest.exists():
        log(f"embeddable cached: {dest.name}")
        return dest
    log(f"downloading {EMBED_URL}")
    urllib.request.urlretrieve(EMBED_URL, dest)
    return dest


def build() -> None:
    dist = ROOT / "web" / "ui" / "dist" / "index.html"
    if not dist.exists():
        raise SystemExit("web/ui/dist is missing. Run `npm run build` in web/ui first.")

    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUNDLE.mkdir(parents=True)

    # 1. Embeddable interpreter.
    py_dir = BUNDLE / "python"
    py_dir.mkdir()
    with zipfile.ZipFile(fetch_embeddable()) as zf:
        zf.extractall(py_dir)
    log("extracted embeddable interpreter")

    # 2. Open up the interpreter path so site-packages and .pth files load.
    pth = py_dir / PTH_NAME
    pth.write_text(
        "python312.zip\n.\nLib\\site-packages\n\nimport site\n", encoding="ascii"
    )

    # 3. Dependencies, installed straight into the bundle.
    site_packages = py_dir / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    log(f"installing {len(RUNTIME_DEPS)} dependencies (this downloads wheels)...")
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--target", str(site_packages),
            "--no-compile",
            *RUNTIME_DEPS,
        ],
        check=True,
    )

    # 4. Application code and data.
    app_dir = BUNDLE / "app"
    for tree in APP_TREES:
        src = ROOT / tree
        shutil.copytree(
            src, app_dir / tree, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    shutil.copytree(ROOT / "web" / "ui" / "dist", app_dir / "web" / "ui" / "dist")
    # web is a namespace package across api/ and ui/; carry its __init__.py.
    for init in (ROOT / "web" / "__init__.py", ROOT / "web" / "api" / "__init__.py"):
        target = app_dir / init.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(init, target)
    log("copied application code, rules, and the built web UI")

    # 5. Supply-chain inventory (CycloneDX SBOM) for infosec/CVE scanning.
    n = write_sbom(site_packages, BUNDLE / "SBOM.json")
    log(f"wrote SBOM.json ({n} components)")

    # 6. Launcher, self-check, and docs.
    (BUNDLE / "run_plumb.py").write_text(RUN_PLUMB, encoding="utf-8")
    (BUNDLE / "run.bat").write_text(RUN_BAT, encoding="ascii", newline="")
    (BUNDLE / "check_plumb.py").write_text(CHECK_PLUMB, encoding="utf-8")
    (BUNDLE / "check.bat").write_text(CHECK_BAT, encoding="ascii", newline="")
    (BUNDLE / "README.txt").write_text(README, encoding="ascii", newline="")
    (BUNDLE / "data" / "reports" / "web").mkdir(parents=True)

    # 7. Zip it.
    OUT_ZIP.parent.mkdir(parents=True, exist_ok=True)
    if OUT_ZIP.exists():
        OUT_ZIP.unlink()
    log("zipping...")
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in BUNDLE.rglob("*"):
            zf.write(path, path.relative_to(BUILD))
    size_mb = OUT_ZIP.stat().st_size / (1024 * 1024)
    log(f"done: {OUT_ZIP}  ({size_mb:.0f} MB)")


if __name__ == "__main__":
    build()

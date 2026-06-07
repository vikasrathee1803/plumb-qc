"""Pull real workbooks from Tableau Cloud and run Plumb's Tableau checks.

Auth is a Connected App direct-trust JWT (HS256, stdlib only), mirroring the
verified pattern in tableau-autopilot/core/cloud.py. Credentials are read
from that project's .cloud.env and never copied into this repo. Downloaded
workbooks land in .tableau_work/ (gitignored).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tableauserverclient as tsc  # noqa: E402

from plumb.checks._tableau import parse_workbook  # noqa: E402
from plumb.config.loader import load_ruleset  # noqa: E402
from plumb.engine.models import Target  # noqa: E402
from plumb.engine.runner import RunRequest, run_checks  # noqa: E402
from plumb.engine.verdict import coverage_caption  # noqa: E402

ENV_FILE = Path(r"C:\Users\test\Projects\tableau-autopilot\.cloud.env")
WORK_DIR = Path(__file__).resolve().parent.parent / ".tableau_work"
DOWNLOAD_SCOPES = ("tableau:content:read", "tableau:workbooks:download")


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_jwt(env: dict[str, str]) -> str:
    issued = int(time.time())
    header = {
        "alg": "HS256",
        "typ": "JWT",
        "kid": env["TABLEAU_CLOUD_CA_SECRET_ID"],
        "iss": env["TABLEAU_CLOUD_CA_CLIENT_ID"],
    }
    claims = {
        "iss": env["TABLEAU_CLOUD_CA_CLIENT_ID"],
        "sub": env["TABLEAU_CLOUD_CA_USER"],
        "aud": "tableau",
        "jti": str(uuid.uuid4()),
        "iat": issued,
        "exp": issued + 540,
        "scp": list(DOWNLOAD_SCOPES),
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    sig = hmac.new(
        env["TABLEAU_CLOUD_CA_SECRET"].encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return signing_input + "." + _b64url(sig)


def main() -> None:
    env = load_env(ENV_FILE)
    WORK_DIR.mkdir(exist_ok=True)
    token = mint_jwt(env)
    auth = tsc.JWTAuth(token, site_id=env["TABLEAU_CLOUD_SITE"])
    server = tsc.Server(env["TABLEAU_CLOUD_SERVER"], use_server_version=True)

    ruleset = load_ruleset(
        Path(__file__).resolve().parent.parent / "rules" / "plumb.yml", enforce_pin=False
    )

    with server.auth.sign_in(auth):
        items, _ = server.workbooks.get()
        print(f"signed in to {env['TABLEAU_CLOUD_SERVER']} site {env['TABLEAU_CLOUD_SITE']}")
        print(f"workbooks on site: {len(items)}")
        for wb in items:
            print(f"  - {wb.name}  (project: {wb.project_name})")
        for wb in items:
            path = Path(server.workbooks.download(wb.id, filepath=str(WORK_DIR)))
            print(f"\n=== {wb.name}  ->  {path.name} ===")
            parsed = parse_workbook(path)
            print(
                f"datasources: {len(parsed.datasources)} | "
                f"calculated fields: {len(parsed.calculated_fields())} | "
                f"worksheets: {len(parsed.worksheets)}"
            )
            result = run_checks(
                RunRequest(
                    target=Target(type="tableau", name=wb.name, source_ref=str(path)),
                    ruleset=ruleset,
                    workbook=parsed,
                )
            )
            print(f"VERDICT: {result.verdict.value}   {coverage_caption(result.coverage)}")
            for c in result.checks:
                if c.status.value in ("FAIL", "WARN"):
                    print(f"  {c.status.value:4} {c.id:11} {c.observed}")


if __name__ == "__main__":
    main()

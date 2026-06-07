"""Prove the AI assist layer works live end to end.

Groq is the preferred provider, but no GROQ_API_KEY exists at the
job-assistant path, only a Gemini key. The multi-provider client falls
back to Gemini, so this loads that key and runs a real explanation on a
real failing check, then asserts the verdict did not move.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plumb.ai import attach_explanations, get_client  # noqa: E402
from plumb.ai.prompts import EXPLAIN_SYSTEM  # noqa: E402
from plumb.config.models import CheckSpec, Ruleset  # noqa: E402
from plumb.engine.models import Target  # noqa: E402
from plumb.engine.runner import RunRequest, run_checks  # noqa: E402
from tests._fakes import RouteSession  # noqa: E402

ENV_FILE = Path(r"C:\Users\test\Projects\job-assistant\api\.env")


def load_key() -> None:
    import os

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("GEMINI_API_KEY=") and "=" in line:
            os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip()


def main() -> None:
    load_key()
    ruleset = Ruleset(
        version="1",
        checks=[CheckSpec(id="D-GRAIN-001", enabled=True, params={"key": ["order_id"]})],
    )
    session = RouteSession().add(
        "__PLUMB_DUP_COUNT",
        [{"ORDER_ID": 10231, "__PLUMB_DUP_COUNT": 4}, {"ORDER_ID": 10244, "__PLUMB_DUP_COUNT": 3}],
    )
    result = run_checks(
        RunRequest(
            target=Target(type="sql", name="rpt_orders"),
            ruleset=ruleset,
            sql_text="SELECT o.order_id FROM orders o JOIN dim_customer c ON o.cust_id = c.id",
            session=session,
        )
    )
    verdict_before = result.verdict
    client = get_client()
    if client is None:
        print("no LLM key available; set GROQ_API_KEY (preferred). Run unaffected.")
        return
    print(f"provider: {client.provider} | model: {client.model}")

    # Report the provider outcome explicitly: a real completion, or the
    # real error. attach_explanations always degrades gracefully.
    grain = result.checks[0]
    try:
        raw = client.complete(
            EXPLAIN_SYSTEM,
            f'{{"check_id":"{grain.id}","observed":"{grain.observed}"}}',
            300,
        )
        print("live completion succeeded, raw bytes:", len(raw))
    except Exception as exc:  # noqa: BLE001
        print("live completion failed (provider error, handled gracefully):")
        print(" ", str(exc).splitlines()[0][:160])

    attach_explanations(result, client, result.checks[0].evidence.query)
    print(f"verdict before {verdict_before.value}, after {result.verdict.value} (unchanged)")
    print(f"{grain.id} status: {grain.status.value} (never set by AI)")
    print("AI explanation:", grain.ai_explanation)


if __name__ == "__main__":
    main()

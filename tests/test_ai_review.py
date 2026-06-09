"""The optional AI advisory check (A-SEM-001). It must stay boxed in: off by
default, WARN/PASS only (never sets a verdict), and graceful when Cortex is off.
Tests inject a fake completion so no warehouse is needed."""

import plumb.checks.ai_review as air
from plumb.ai.client import AIClient
from plumb.engine.models import Severity, Status
from tests._fakes import make_ctx


def _client(reply: str) -> AIClient:
    return AIClient(complete=lambda system, user, max_tokens: reply)


def test_ai_review_warns_advisory_on_concerns(monkeypatch):
    reply = "- revenue sums gross, ignoring refunds\n- filter uses load_date not event_date"
    monkeypatch.setattr(air, "cortex_enabled", lambda: True)
    monkeypatch.setattr(air, "get_client", lambda **k: _client(reply))
    res = air.a_sem_001(make_ctx("SELECT SUM(amount) FROM sales", session=object()), {})
    assert res.status is Status.WARN  # a note, never FAIL/BLOCKER
    assert res.severity is Severity.LOW
    assert len(res.evidence.sample_rows) == 2
    assert "refunds" in str(res.evidence.sample_rows[0])


def test_ai_review_passes_when_model_says_ok(monkeypatch):
    monkeypatch.setattr(air, "cortex_enabled", lambda: True)
    monkeypatch.setattr(air, "get_client", lambda **k: _client("OK"))
    res = air.a_sem_001(make_ctx("SELECT 1 AS x", session=object()), {})
    assert res.status is Status.PASS


def test_ai_review_skips_when_assist_off(monkeypatch):
    monkeypatch.setattr(air, "cortex_enabled", lambda: False)
    res = air.a_sem_001(make_ctx("SELECT 1", session=object()), {})
    assert res.status is Status.SKIP


def test_ai_review_skips_when_model_errors(monkeypatch):
    # the completion call raising must never break a run, only SKIP
    def explode(*args: object) -> str:
        raise RuntimeError("cortex not provisioned")

    monkeypatch.setattr(air, "cortex_enabled", lambda: True)
    monkeypatch.setattr(air, "get_client", lambda **k: AIClient(complete=explode))
    res = air.a_sem_001(make_ctx("SELECT 1", session=object()), {})
    assert res.status is Status.SKIP


def test_ai_review_never_returns_a_verdict_setting_status(monkeypatch):
    # whatever the model says, the check can only PASS or WARN, never FAIL/BLOCKER
    monkeypatch.setattr(air, "cortex_enabled", lambda: True)
    monkeypatch.setattr(air, "get_client", lambda **k: _client("- this build is completely wrong"))
    res = air.a_sem_001(make_ctx("SELECT 1", session=object()), {})
    assert res.status in (Status.PASS, Status.WARN)

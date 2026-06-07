"""AI assist layer. The invariant under test: it runs only on decided
results and never changes a status or verdict. Plus graceful degradation."""

import json

from plumb.ai import draft_fix, draft_recon_sql
from plumb.ai.client import AIClient
from plumb.ai.explain import attach_explanations, explain_failure
from plumb.ai.parser import extract_json
from plumb.config.models import CheckSpec, Ruleset
from plumb.engine.models import Status, Target, Verdict
from plumb.engine.runner import RunRequest, run_checks
from tests._fakes import RouteSession


def client_returning(text: str) -> AIClient:
    return AIClient(complete=lambda system, user, max_tokens: text)


def client_raising() -> AIClient:
    def boom(system, user, max_tokens):
        raise RuntimeError("network down")

    return AIClient(complete=boom)


EXPLAIN_JSON = json.dumps(
    {
        "root_cause": "The join to dim_customer is at a finer grain than orders.",
        "business_impact": "Revenue is overstated about 4x for affected orders.",
        "confidence": "high",
    }
)


class TestParser:
    def test_plain_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_with_prose_around_it(self):
        assert extract_json('Sure!\n{"a": 1}\nHope that helps') == {"a": 1}

    def test_garbage_returns_none(self):
        assert extract_json("not json at all") is None

    def test_empty_returns_none(self):
        assert extract_json("") is None
        assert extract_json(None) is None

    def test_nested_braces(self):
        assert extract_json('{"a": {"b": 2}}') == {"a": {"b": 2}}


def _blocked_result():
    ruleset = Ruleset(
        version="1",
        checks=[CheckSpec(id="D-GRAIN-001", enabled=True, params={"key": ["order_id"]})],
    )
    session = RouteSession().add(
        "__PLUMB_DUP_COUNT", [{"ORDER_ID": 1, "__PLUMB_DUP_COUNT": 4}]
    )
    return run_checks(
        RunRequest(
            target=Target(type="sql", name="t"),
            ruleset=ruleset,
            sql_text="SELECT order_id FROM o JOIN c ON o.cid = c.id",
            session=session,
        )
    )


class TestExplainNeverChangesVerdict:
    def test_verdict_and_statuses_identical_with_and_without_explain(self):
        without = _blocked_result()
        with_ai = _blocked_result()

        before = (
            with_ai.verdict,
            [(c.id, c.status, c.severity) for c in with_ai.checks],
            with_ai.summary.model_dump(),
            with_ai.coverage.model_dump(),
        )
        attach_explanations(with_ai, client_returning(EXPLAIN_JSON), with_ai.checks[0].name)
        after = (
            with_ai.verdict,
            [(c.id, c.status, c.severity) for c in with_ai.checks],
            with_ai.summary.model_dump(),
            with_ai.coverage.model_dump(),
        )

        assert before == after  # nothing but ai_explanation changed
        assert without.verdict is with_ai.verdict is Verdict.BLOCKED

    def test_explanation_is_attached_to_the_failing_check(self):
        result = _blocked_result()
        attach_explanations(result, client_returning(EXPLAIN_JSON), "sql")
        grain = next(c for c in result.checks if c.id == "D-GRAIN-001")
        assert grain.status is Status.FAIL
        assert grain.ai_explanation is not None
        assert "finer grain" in grain.ai_explanation
        assert "confidence: high" in grain.ai_explanation


class TestGracefulDegradation:
    def test_parse_failure_leaves_explanation_none(self):
        result = _blocked_result()
        attach_explanations(result, client_returning("the model rambled, no json"), "sql")
        grain = next(c for c in result.checks if c.id == "D-GRAIN-001")
        assert grain.status is Status.FAIL
        assert grain.ai_explanation is None

    def test_client_exception_leaves_explanation_none_and_verdict_intact(self):
        result = _blocked_result()
        attach_explanations(result, client_raising(), "sql")
        assert result.verdict is Verdict.BLOCKED
        assert all(c.ai_explanation is None for c in result.checks)

    def test_explain_failure_returns_none_on_missing_keys(self):
        client = client_returning('{"unexpected": "shape"}')
        check = _blocked_result().checks[0]
        assert explain_failure(client, check, "sql") is None


class TestFixAndRecon:
    def test_draft_fix_parses(self):
        client = client_returning(
            json.dumps(
                {
                    "explanation": "add aggregation",
                    "patch": "GROUP BY 1",
                    "needs_human_review": True,
                }
            )
        )
        check = _blocked_result().checks[0]
        out = draft_fix(client, check, "sql")
        assert out is not None
        assert out["patch"] == "GROUP BY 1"
        assert out["needs_human_review"] is True

    def test_draft_fix_handles_null_patch(self):
        client = client_returning(json.dumps({"explanation": "unclear", "patch": None}))
        out = draft_fix(client, _blocked_result().checks[0], "sql")
        assert out is not None
        assert out["patch"] is None
        assert out["needs_human_review"] is True

    def test_draft_recon_sql_with_blocking_question(self):
        client = client_returning(
            json.dumps({"sql": None, "assumptions": [], "blocking_question": "which date grain?"})
        )
        out = draft_recon_sql(client, "total revenue last month", ["FCT_SALES"])
        assert out is not None
        assert out["sql"] is None
        assert out["blocking_question"] == "which date grain?"

    def test_draft_recon_sql_returns_query(self):
        client = client_returning(
            json.dumps({"sql": "SELECT SUM(amount) FROM FCT_SALES", "assumptions": ["gross"]})
        )
        out = draft_recon_sql(client, "sum of amount", ["FCT_SALES"])
        assert out is not None
        assert "SUM(amount)" in out["sql"]
        assert out["assumptions"] == ["gross"]

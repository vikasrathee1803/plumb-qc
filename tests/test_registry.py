"""Contract tests for the check registry plugin seam."""

import pytest

from plumb.engine.models import (
    CheckFamily,
    CheckResult,
    ExecutionType,
    Severity,
    Status,
)
from plumb.engine.registry import (
    CheckContext,
    DuplicateCheckError,
    UnknownCheckError,
    all_checks,
    checks_by_family,
    get_check,
    is_registered,
    register_check,
    registry_snapshot,
    reset_registry,
    restore_registry,
)


@pytest.fixture(autouse=True)
def clean_registry():
    snapshot = registry_snapshot()
    reset_registry()
    yield
    restore_registry(snapshot)


def _result(check_id: str) -> CheckResult:
    return CheckResult(
        id=check_id,
        name="x",
        family=CheckFamily.STATIC,
        severity=Severity.LOW,
        status=Status.PASS,
    )


def test_register_and_get() -> None:
    @register_check(
        check_id="S-STAT-001",
        name="SELECT * in a production query",
        family=CheckFamily.STATIC,
        default_severity=Severity.HIGH,
        execution_type=ExecutionType.STATIC,
    )
    def my_check(ctx: CheckContext, params: dict) -> CheckResult:
        return _result("S-STAT-001")

    definition = get_check("S-STAT-001")
    assert definition.check_id == "S-STAT-001"
    assert definition.name == "SELECT * in a production query"
    assert definition.family is CheckFamily.STATIC
    assert definition.default_severity is Severity.HIGH
    assert definition.execution_type is ExecutionType.STATIC
    assert definition.fn is my_check
    assert is_registered("S-STAT-001")


def test_decorator_returns_function_unchanged() -> None:
    def raw(ctx: CheckContext, params: dict) -> CheckResult:
        return _result("D-NULL-001")

    decorated = register_check(
        check_id="D-NULL-001",
        name="key not null",
        family=CheckFamily.ASSERTIONS,
        default_severity=Severity.BLOCKER,
        execution_type=ExecutionType.EXECUTION,
    )(raw)
    assert decorated is raw


def test_duplicate_id_raises() -> None:
    def fn(ctx: CheckContext, params: dict) -> CheckResult:
        return _result("X-1")

    register_check(
        check_id="X-1",
        name="first",
        family=CheckFamily.STATIC,
        default_severity=Severity.LOW,
        execution_type=ExecutionType.STATIC,
    )(fn)
    with pytest.raises(DuplicateCheckError):
        register_check(
            check_id="X-1",
            name="second",
            family=CheckFamily.STATIC,
            default_severity=Severity.LOW,
            execution_type=ExecutionType.STATIC,
        )(fn)


def test_unknown_id_raises_with_clear_message() -> None:
    with pytest.raises(UnknownCheckError, match="NOPE-001"):
        get_check("NOPE-001")


def test_by_family_filters_and_all_checks_sorted() -> None:
    def fn(ctx: CheckContext, params: dict) -> CheckResult:
        return _result("any")

    for check_id, family in [
        ("D-GRAIN-001", CheckFamily.ASSERTIONS),
        ("S-STAT-002", CheckFamily.STATIC),
        ("D-RECON-001", CheckFamily.ASSERTIONS),
    ]:
        register_check(
            check_id=check_id,
            name=check_id,
            family=family,
            default_severity=Severity.BLOCKER,
            execution_type=ExecutionType.EXECUTION,
        )(fn)

    assertion_ids = [d.check_id for d in checks_by_family(CheckFamily.ASSERTIONS)]
    assert assertion_ids == ["D-GRAIN-001", "D-RECON-001"]
    assert [d.check_id for d in all_checks()] == [
        "D-GRAIN-001",
        "D-RECON-001",
        "S-STAT-002",
    ]

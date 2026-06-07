"""Check registration and the plugin seam.

Adding a check to Plumb is dropping a decorated function into a checks/
module. The runner discovers checks here; it never imports check modules
for their contents. Nothing in the engine changes when the catalog grows.

A check function receives a CheckContext plus its resolved params and
returns one CheckResult or a list of them. It must decide its status
deterministically. It must never issue a non-read statement; execution
checks go through the session wrapper, which enforces that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Union

from plumb.engine.models import (
    CheckFamily,
    CheckResult,
    ExecutionType,
    Severity,
    Target,
)


@dataclass
class CheckContext:
    """Everything a check may need. Static checks use sql_text only.
    Metadata and execution checks use the session. The runner builds one
    context per run and passes it to every check."""

    run_id: str
    target: Target
    sql_text: str | None = None
    session: Any | None = None
    ruleset: Any | None = None
    baseline_store: Any | None = None
    extras: dict[str, Any] = field(default_factory=dict)


CheckFn = Callable[[CheckContext, dict[str, Any]], Union[CheckResult, list[CheckResult]]]


@dataclass(frozen=True)
class CheckDefinition:
    """A registered check: identity, classification, and the callable."""

    check_id: str
    name: str
    family: CheckFamily
    default_severity: Severity
    execution_type: ExecutionType
    fn: CheckFn


class DuplicateCheckError(Exception):
    """A check id was registered twice. Check ids are globally unique."""


class UnknownCheckError(Exception):
    """A ruleset referenced a check id that is not in the registry."""


_REGISTRY: dict[str, CheckDefinition] = {}


def register_check(
    *,
    check_id: str,
    name: str,
    family: CheckFamily,
    default_severity: Severity,
    execution_type: ExecutionType,
) -> Callable[[CheckFn], CheckFn]:
    """Decorator that registers a check function under its catalog id."""

    def decorator(fn: CheckFn) -> CheckFn:
        if check_id in _REGISTRY:
            raise DuplicateCheckError(
                f"check id {check_id!r} is already registered "
                f"by {_REGISTRY[check_id].fn.__module__}"
            )
        _REGISTRY[check_id] = CheckDefinition(
            check_id=check_id,
            name=name,
            family=family,
            default_severity=default_severity,
            execution_type=execution_type,
            fn=fn,
        )
        return fn

    return decorator


def get_check(check_id: str) -> CheckDefinition:
    try:
        return _REGISTRY[check_id]
    except KeyError:
        raise UnknownCheckError(
            f"check id {check_id!r} is not registered; known ids: "
            f"{sorted(_REGISTRY) or 'none'}"
        ) from None


def all_checks() -> tuple[CheckDefinition, ...]:
    return tuple(_REGISTRY[k] for k in sorted(_REGISTRY))


def checks_by_family(family: CheckFamily) -> tuple[CheckDefinition, ...]:
    return tuple(d for d in all_checks() if d.family is family)


def is_registered(check_id: str) -> bool:
    return check_id in _REGISTRY


def reset_registry() -> None:
    """Test seam only. Production code never calls this."""
    _REGISTRY.clear()


def registry_snapshot() -> dict[str, CheckDefinition]:
    """Test seam: capture the registry so a test can restore it after
    mutating. Check modules register at import time, so a test that
    clears the registry without restoring would break later tests."""
    return dict(_REGISTRY)


def restore_registry(snapshot: dict[str, CheckDefinition]) -> None:
    """Test seam: restore a snapshot taken with registry_snapshot."""
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)

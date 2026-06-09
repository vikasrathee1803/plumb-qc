"""Test doubles for the read-only Snowflake session.

RouteSession matches each executed SQL against a list of substring rules
and returns the first match's rows, so a check's generated queries can be
answered deterministically offline. It records every statement so tests
can assert the query tag path and that only reads were issued.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from plumb.config.models import Ruleset
from plumb.connect.snowflake import assert_read_only
from plumb.engine.models import Target
from plumb.engine.registry import CheckContext


@dataclass
class _Profile:
    warehouse: str = "PLUMB_WH"
    role: str = "PLUMB_QC_ROLE"


@dataclass
class RouteSession:
    """A fake session. routes is a list of (needle, rows); the first route
    whose needle is a substring of the SQL wins. enforce_read_only mirrors
    the real guard so tests catch a check that builds a non-read."""

    routes: list[tuple[str, list[dict[str, Any]]]] = field(default_factory=list)
    default_rows: list[dict[str, Any]] = field(default_factory=list)
    enforce_read_only: bool = True
    executed: list[str] = field(default_factory=list)
    query_tag: str = "plumb_qc:test-run"
    profile: _Profile = field(default_factory=_Profile)
    # Columns the build-output probe (SELECT * ... WHERE 1 = 0) reports.
    build_columns: list[str] = field(default_factory=list)

    def add(self, needle: str, rows: list[dict[str, Any]]) -> "RouteSession":
        self.routes.append((needle, rows))
        return self

    def execute(self, sql: str, params: Any = None) -> SimpleNamespace:
        if self.enforce_read_only:
            assert_read_only(sql)
        self.executed.append(sql)
        cols = list(self.build_columns) if "WHERE 1 = 0" in sql else []
        for needle, rows in self.routes:
            if needle in sql:
                return SimpleNamespace(rows=rows, truncated=False, query_id="fake", columns=cols)
        return SimpleNamespace(
            rows=list(self.default_rows), truncated=False, query_id="fake", columns=cols
        )

    def close(self) -> None:
        pass


def make_ctx(
    sql: str | None = None,
    *,
    session: Any = None,
    ruleset: Ruleset | None = None,
    baseline_store: Any = None,
    baseline_name: str | None = None,
) -> CheckContext:
    return CheckContext(
        run_id="test-run",
        target=Target(type="sql", name="t", source_ref=None),
        sql_text=sql,
        session=session,
        ruleset=ruleset or Ruleset(version="1"),
        baseline_store=baseline_store,
        extras={"baseline_name": baseline_name},
    )


def callable_session(fn: Callable[[str], list[dict[str, Any]]]) -> Any:
    """A fake session backed by an arbitrary sql -> rows function."""

    class _Fn:
        query_tag = "plumb_qc:test-run"
        profile = _Profile()
        executed: list[str] = []

        def execute(self, sql: str, params: Any = None) -> SimpleNamespace:
            assert_read_only(sql)
            self.executed.append(sql)
            return SimpleNamespace(rows=fn(sql), truncated=False, query_id="fake")

        def close(self) -> None:
            pass

    return _Fn()

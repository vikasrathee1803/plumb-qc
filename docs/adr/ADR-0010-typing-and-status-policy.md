# ADR-0010: Typing strategy and the static FAIL vs WARN policy

Date: 2026-06-07. Status: accepted.

## Typing strategy

mypy runs strict (disallow_untyped_defs, no_implicit_optional) across the
engine, config, connect, baseline, report, and the check helper modules
(_sql, _base, _metadata). The five check-implementation modules
(sql_static, sql_meta, sql_assertions, sql_regression, sql_performance)
relax disallow_untyped_defs and disable union-attr and arg-type. Reason:
every check shares one CheckContext whose sql_text and session are
Optional by design so a check can run static-only. Each check's own SKIP
guard establishes non-None, but that postcondition is not expressible to
mypy without an assert in every check. The checks are pure and covered by
fixture tests (one or more per family), so the residual risk is low and
the core stays strict. Third-party packages without stubs (pyarrow,
snowflake, sqlfluff, keyring) are set ignore_missing_imports.

## Static check status policy

A static check returns FAIL for a definitive structural fault (SELECT *,
cartesian join, NOT IN subquery, a referenced deprecated object) and WARN
for a heuristic that needs human judgment (implicit cast, non-SARGable
predicate, hardcoded date, implicit join type, DISTINCT over a join, lint
style). Per the verdict model a WARN is always a note and never escalates,
which matches the spec's definition of WARN: ran but cannot fully assert.
An unparseable target yields a single ERROR per check so it is surfaced,
never silently passed.

Reversibility: cheap. Status choices are per check; the typing override is
one config block.

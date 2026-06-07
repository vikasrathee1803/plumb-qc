# ADR-0007: Toolchain and dependency pins

Date: 2026-06-07. Status: accepted.

- Package requires-python >=3.11 per the spec; development and CI run
  Python 3.12 (the CI image is python:3.12-slim) for guaranteed binary
  wheel coverage across snowflake-connector-python and pyarrow.
- All runtime dependencies are pinned exact (==) in pyproject.toml per
  the no-unpinned-dependencies invariant. Upgrades are deliberate commits
  that rerun the full suite, never ambient.
- Lint is ruff with T20 (no print) and E722 (no bare except) enforcing
  two of the forbidden patterns mechanically. Types are mypy with
  disallow_untyped_defs.

Reversibility: cheap for individual pins, locked for the pinning policy
itself.

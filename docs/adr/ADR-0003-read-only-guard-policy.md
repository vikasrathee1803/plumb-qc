# ADR-0003: Read-only guard policy

Date: 2026-06-07. Status: accepted.

The spec mandates the engine refuses anything that is not a read. The
exact allowed set was open. Decision, implemented in
plumb/connect/snowflake.py assert_read_only:

- Allowed: exactly one statement per execute call, rooted in a SELECT
  read (including WITH and set operations), or EXPLAIN of one.
- Refused: everything else, including SHOW and DESCRIBE (metadata checks
  read INFORMATION_SCHEMA via SELECT instead), multi-statement SQL,
  comment-only input, and anything sqlglot cannot parse. Unparseable SQL
  falls back to sqlglot's Command node, which is refused: fail closed.
- Defense in depth: root allowlist (Select, SetOperation, Subquery) plus
  a walk of the whole tree refusing write and side-effect nodes (Insert,
  Update, Delete, Merge, Create, Drop, Alter, Command, TruncateTable,
  Copy, Grant, Use, Set, Transaction, Commit, Rollback, LoadData).
- EXPLAIN is unwrapped textually (sqlglot parses it as Command in the
  snowflake dialect) and the inner statement is validated; EXPLAIN of a
  write is refused.
- Session parameters (QUERY_TAG, STATEMENT_TIMEOUT_IN_SECONDS) are set
  via connector session_parameters at connect time, never via ALTER
  SESSION, so the guard needs no carve-out for our own setup.

Proof: tests/test_readonly_guard.py covers 29 refusal cases and 11
allowed reads.

Reversibility: cheap to loosen (for example allowing SHOW) by editing the
allowlist with a new test; the fail-closed default is the safe baseline.

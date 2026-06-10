"""Tests for the custom-SQL projection extractor (plumb/parity/sql_projection.py).

Invariants under test (PARITY-PLAN-V2 S9.1 / D15): extract_projected_columns
NEVER raises — every refusal is None; a star anywhere in the final projection
refuses; aggregates and windows are skipped (aggregates of aggregates are
wrong) while plain expressions are kept; aggregate-only projections return []
(row-count-only, same as v1); names are upper-cased Snowflake-canonical and
colliding output names are dropped entirely.
"""

from __future__ import annotations

from plumb.parity.sql_projection import extract_projected_columns


class TestPlainProjections:
    def test_simple_select_plain_columns(self) -> None:
        assert extract_projected_columns("SELECT a, b FROM t") == ["A", "B"]

    def test_aliased_columns_use_alias(self) -> None:
        assert extract_projected_columns(
            "SELECT revenue AS rev, cost AS c FROM t"
        ) == ["REV", "C"]

    def test_qualified_column_uses_column_part_only(self) -> None:
        assert extract_projected_columns("SELECT t.d, t.e FROM t") == ["D", "E"]

    def test_names_uppercased(self) -> None:
        assert extract_projected_columns("select revenue, cost as c from t") == [
            "REVENUE",
            "C",
        ]

    def test_unaliased_expression_named_by_sql_text(self) -> None:
        assert extract_projected_columns("SELECT a + b FROM t") == ["A + B"]

    def test_case_expression_named_by_sql_text(self) -> None:
        assert extract_projected_columns(
            "SELECT CASE WHEN a > 1 THEN 1 ELSE 0 END FROM t"
        ) == ["CASE WHEN A > 1 THEN 1 ELSE 0 END"]

    def test_projection_order_preserved(self) -> None:
        assert extract_projected_columns("SELECT z, a, m FROM t") == ["Z", "A", "M"]

    def test_parenthesized_select_unwrapped(self) -> None:
        assert extract_projected_columns("(SELECT a FROM t)") == ["A"]

    def test_trailing_semicolon_still_single_statement(self) -> None:
        # metrics.py refuses bare semicolons separately; this module only
        # cares that it is one statement.
        assert extract_projected_columns("SELECT a FROM t;") == ["A"]


class TestAggregatesAndWindows:
    def test_mixed_aggregates_skipped_plain_kept(self) -> None:
        assert extract_projected_columns(
            "SELECT region, SUM(amount) AS total, AVG(x) FROM t GROUP BY region"
        ) == ["REGION"]

    def test_aggregate_only_projection_returns_empty_list(self) -> None:
        result = extract_projected_columns("SELECT SUM(a) AS s, COUNT(b) FROM t")
        assert result == []
        assert result is not None

    def test_count_star_is_aggregate_not_star_refusal(self) -> None:
        # The star inside COUNT(*) is not a projection star: the statement
        # stays parseable and the aggregate is skipped.
        assert extract_projected_columns("SELECT COUNT(*), a FROM t GROUP BY a") == ["A"]

    def test_aggregate_inside_arithmetic_skipped(self) -> None:
        assert extract_projected_columns(
            "SELECT SUM(a) + 1 AS s, b FROM t GROUP BY b"
        ) == ["B"]

    def test_window_function_skipped(self) -> None:
        assert extract_projected_columns(
            "SELECT ROW_NUMBER() OVER (ORDER BY a) AS rn, a FROM t"
        ) == ["A"]

    def test_unaliased_window_skipped(self) -> None:
        assert extract_projected_columns("SELECT a, SUM(b) OVER () FROM t") == ["A"]


class TestRefusals:
    def test_bare_star_returns_none(self) -> None:
        assert extract_projected_columns("SELECT * FROM t") is None

    def test_qualified_star_returns_none(self) -> None:
        assert extract_projected_columns("SELECT t.*, a FROM t") is None

    def test_star_exclude_returns_none(self) -> None:
        assert extract_projected_columns("SELECT * EXCLUDE (a) FROM t") is None

    def test_unparseable_returns_none(self) -> None:
        assert extract_projected_columns("SELECT FROM WHERE !!") is None

    def test_multiple_statements_return_none(self) -> None:
        assert extract_projected_columns("SELECT 1; SELECT 2") is None

    def test_update_returns_none(self) -> None:
        assert extract_projected_columns("UPDATE t SET a = 1") is None

    def test_insert_returns_none(self) -> None:
        assert extract_projected_columns("INSERT INTO t VALUES (1)") is None

    def test_ddl_returns_none(self) -> None:
        assert extract_projected_columns("DROP TABLE t") is None

    def test_empty_and_whitespace_return_none(self) -> None:
        assert extract_projected_columns("") is None
        assert extract_projected_columns("   \n  ") is None

    def test_never_raises_on_garbage(self) -> None:
        for garbage in ("(((", "!!!", "WITH", "SELECT", ");--", "\x00", "select )"):
            assert extract_projected_columns(garbage) in (None, [])


class TestCteAndSetOperations:
    def test_cte_uses_final_select_projection(self) -> None:
        assert extract_projected_columns(
            "WITH m AS (SELECT 1 AS one) SELECT one, two FROM m"
        ) == ["ONE", "TWO"]

    def test_union_takes_leftmost_branch(self) -> None:
        assert extract_projected_columns(
            "SELECT a FROM x UNION ALL SELECT b FROM y"
        ) == ["A"]

    def test_cte_wrapped_union_takes_leftmost_branch(self) -> None:
        assert extract_projected_columns(
            "WITH m AS (SELECT 1 AS one) SELECT one FROM m UNION SELECT two FROM n"
        ) == ["ONE"]

    def test_star_in_leftmost_union_branch_refuses(self) -> None:
        assert extract_projected_columns("SELECT * FROM x UNION SELECT b FROM y") is None

    def test_star_inside_cte_body_is_fine(self) -> None:
        # Only the FINAL projection matters; the CTE body's star never
        # reaches the output column list.
        assert extract_projected_columns(
            "WITH m AS (SELECT * FROM t) SELECT a, b FROM m"
        ) == ["A", "B"]


class TestNameCollisions:
    def test_duplicate_output_names_dropped_entirely(self) -> None:
        # Referencing "A" in the metrics wrapper would be ambiguous in the
        # warehouse, so every colliding occurrence is dropped, not just
        # the later ones.
        assert extract_projected_columns("SELECT a, b, a FROM t") == ["B"]

    def test_alias_colliding_with_column_dropped(self) -> None:
        assert extract_projected_columns("SELECT a, b AS a, c FROM t") == ["C"]

    def test_collision_with_skipped_aggregate_alias_dropped(self) -> None:
        # The aggregate is skipped, but its output name still exists in
        # the result and would make the kept "A" ambiguous.
        assert extract_projected_columns(
            "SELECT a, SUM(b) AS a FROM t GROUP BY a"
        ) == []

    def test_collision_detected_case_insensitively(self) -> None:
        # Unquoted identifiers fold to the same canonical name.
        assert extract_projected_columns("SELECT a, A, b FROM t") == ["B"]


class TestWeirdAliases:
    def test_quoted_alias_kept_verbatim_uppercased(self) -> None:
        assert extract_projected_columns('SELECT a AS "ALIASED NAME" FROM t') == [
            "ALIASED NAME"
        ]

    def test_alias_with_embedded_quote_returned_unescaped(self) -> None:
        # The raw name (one double quote) is returned; quoting/escaping is
        # the caller's job via metrics._quote_ident.
        assert extract_projected_columns('SELECT a AS "Weird ""Name" FROM t') == [
            'WEIRD "NAME'
        ]

    def test_quoted_lowercase_alias_uppercased_canonically(self) -> None:
        # Upper-casing is the documented canonical form; a quoted
        # lower-case alias will simply fail the wrapper at query time and
        # degrade to row-count-only — never a wrong measurement.
        assert extract_projected_columns('SELECT a AS "lower" FROM t') == ["LOWER"]

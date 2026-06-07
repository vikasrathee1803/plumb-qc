"""Lock the shape of the SQL the execution checks generate.

These are the queries Plumb actually sends to Snowflake. Bugs here are
invisible to fake-session tests (which mock the result), so they are
asserted directly. Several were caught against live Snowflake.
"""

from plumb.checks import _sql
from plumb.connect.snowflake import assert_read_only

TARGET = "SELECT a, b FROM db.s.t"


def test_every_builder_emits_a_single_read():
    queries = [
        _sql.grain_count_query(TARGET, ["a"]),
        _sql.null_count_query(TARGET, ["a", "b"]),
        _sql.row_count_query(TARGET),
        _sql.full_dup_query(TARGET),
        _sql.freshness_query(TARGET, "a"),
        _sql.select_all_query(TARGET, 100),
        _sql.domain_violation_query(TARGET, "a", [1, 2, 3]),
        _sql.range_violation_query(TARGET, "a", 0, 10),
        _sql.orphan_query(TARGET, "a", "db.s.dim", "id"),
    ]
    for q in queries:
        assert_read_only(q)  # must pass the read-only guard


def test_full_dup_query_groups_by_the_whole_row():
    """Regression: SELECT * (not COUNT(*)) so GROUP BY ALL groups by every
    column. The COUNT(*) form grouped by nothing and always reported a dup."""
    q = _sql.full_dup_query(TARGET)
    assert "SELECT * FROM __plumb_target GROUP BY ALL HAVING COUNT(*) > 1" in q


def test_identifiers_are_not_force_quoted():
    """A user-declared lowercase key must resolve case-insensitively on
    Snowflake, so identifiers are emitted bare, not double-quoted."""
    q = _sql.grain_count_query(TARGET, ["customer_id"])
    assert "customer_id" in q
    assert '"customer_id"' not in q


def test_domain_literals_are_safely_rendered():
    q = _sql.domain_violation_query(TARGET, "status", ["A", "B'; DROP", 3])
    assert_read_only(q)  # injection attempt stays inside a quoted literal
    assert "DROP TABLE" not in q.upper().replace("'", "")


def test_recon_template_renders_target_as_subquery():
    rendered = _sql.render_target_template("SELECT SUM(x) FROM {{ target }}", TARGET)
    assert "{{ target }}" not in rendered
    assert "(SELECT" in rendered.replace("\n", " ").upper().replace("( SELECT", "(SELECT")

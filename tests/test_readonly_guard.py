"""The test the spec mandates: prove the engine refuses any statement
that is not a read. Fail closed on everything ambiguous."""

import pytest

from plumb.connect.snowflake import ReadOnlyViolation, assert_read_only

REFUSED = [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "DELETE FROM t WHERE a = 1",
    "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.a = s.a",
    "CREATE TABLE t (a INT)",
    "CREATE OR REPLACE VIEW v AS SELECT 1",
    "CREATE TABLE t AS SELECT * FROM s",
    "DROP TABLE t",
    "ALTER TABLE t ADD COLUMN c INT",
    "ALTER SESSION SET QUERY_TAG = 'spoofed'",
    "TRUNCATE TABLE t",
    "COPY INTO t FROM @stage",
    "CALL my_proc()",
    "GRANT SELECT ON t TO ROLE r",
    "USE WAREHOUSE big_expensive_wh",
    "SET v = 1",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "PUT file://local.csv @stage",
    "SHOW TABLES",
    "DESCRIBE TABLE t",
    "SELECT 1; DROP TABLE t",
    "/* sneaky comment */ DROP TABLE t",
    "EXPLAIN DELETE FROM t",
    "FROBNICATE ALL THE THINGS",
    "",
    "   ",
    "-- just a comment",
]

ALLOWED = [
    "SELECT 1",
    "SELECT 1;",
    "SELECT a, b FROM db.sch.t WHERE a > 1",
    "WITH x AS (SELECT 1 AS a) SELECT * FROM x",
    "SELECT a FROM t1 UNION ALL SELECT a FROM t2",
    "SELECT a FROM t1 INTERSECT SELECT a FROM t2",
    "(SELECT 1)",
    "EXPLAIN SELECT a FROM t",
    "EXPLAIN USING JSON SELECT a FROM t",
    "SELECT t.a, u.b FROM t JOIN u ON t.id = u.id",
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'MART'",
]


@pytest.mark.parametrize("sql", REFUSED, ids=lambda s: repr(s[:48]))
def test_non_read_is_refused(sql: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        assert_read_only(sql)


@pytest.mark.parametrize("sql", ALLOWED, ids=lambda s: repr(s[:48]))
def test_read_is_allowed(sql: str) -> None:
    assert_read_only(sql)

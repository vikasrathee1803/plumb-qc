"""Reducing a complex build (view, CTAS, multi-step script) to one read query,
so the same checks, lineage, and read-only guarantees apply."""

import pytest

from plumb.connect.snowflake import assert_read_only
from plumb.engine.buildquery import BuildExtractError, extract_build_query


def test_bare_select_passes_through():
    bq = extract_build_query("SELECT a, b FROM t")
    assert "SELECT" in bq.sql.upper()
    assert bq.notes == [] and bq.target_name is None


def test_view_extracts_its_select():
    bq = extract_build_query("CREATE OR REPLACE VIEW v AS SELECT a, SUM(b) AS tot FROM t GROUP BY a")  # noqa: E501
    assert bq.sql.strip().upper().startswith("SELECT")
    assert "CREATE" not in bq.sql.upper()
    assert bq.notes and "VIEW" in bq.notes[0]


def test_ctas_keeps_inner_ctes():
    bq = extract_build_query(
        "CREATE TABLE final AS WITH s AS (SELECT a, b FROM raw) SELECT a, SUM(b) FROM s GROUP BY a"
    )
    assert bq.sql.upper().startswith("WITH")
    assert "CREATE" not in bq.sql.upper()
    assert bq.target_name == "final"


def test_multistep_script_folds_steps_into_ctes():
    sql = (
        "USE WAREHOUSE WH;\n"
        "CREATE OR REPLACE TEMP TABLE stg AS SELECT id, amount FROM raw WHERE amount > 0;\n"
        "CREATE OR REPLACE TABLE daily AS SELECT id, SUM(amount) AS total FROM stg GROUP BY id;"
    )
    bq = extract_build_query(sql)
    assert bq.sql.upper().startswith("WITH STG AS")
    assert "CREATE" not in bq.sql.upper() and "USE WAREHOUSE" not in bq.sql.upper()
    assert bq.target_name == "daily"
    assert "folded 1" in bq.notes[0] and "skipped 1" in bq.notes[0]
    assert_read_only(bq.sql)  # the folded build is a single read; does not raise


def test_trailing_select_uses_prior_steps_as_ctes():
    sql = "CREATE TEMP TABLE a AS SELECT 1 AS x;\nSELECT x FROM a WHERE x > 0;"
    bq = extract_build_query(sql)
    assert bq.sql.upper().startswith("WITH A AS")
    assert bq.sql.rstrip().upper().endswith("X > 0")
    assert_read_only(bq.sql)


def test_no_read_to_analyze_raises():
    with pytest.raises(BuildExtractError):
        extract_build_query("USE WAREHOUSE WH;\nCREATE TABLE t (a INT);")


def test_unparseable_raises():
    with pytest.raises(BuildExtractError):
        extract_build_query("SELEKT )(")

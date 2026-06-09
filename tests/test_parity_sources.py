"""Tests for plumb.parity.sources (PARITY-PLAN S2.1).

Every fixture in tests/_parity_fixtures.py produces exactly the expected
relation set, refusals carry machine-readable reasons, and the bare-table /
published / legacy-direct shapes are covered with inline workbooks.
"""

from __future__ import annotations

import pytest

from plumb.checks._tableau import TableauParseError
from plumb.parity.contracts import (
    REFUSAL_EXTRACT_ONLY,
    REFUSAL_JOIN,
    REFUSAL_UNION,
)
from plumb.parity.sources import REFUSAL_PUBLISHED, extract_relations
from tests._parity_fixtures import (
    TWB_CUSTOM_SQL,
    TWB_EXTRACT_ONLY,
    TWB_EXTRACT_OVER_LIVE,
    TWB_JOIN,
    TWB_MALFORMED,
    TWB_TWO_TABLES,
    write_twb,
)

_HEADER = "<?xml version='1.0' encoding='utf-8' ?>\n"

TWB_BARE_TABLE = (
    _HEADER
    + """
<workbook version='18.1'>
  <datasources>
    <datasource caption='Bare Table' name='federated.bare0'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='acme' name='snowflake.bare1'>
            <connection class='snowflake' dbname='LEGACY_DB' schema='SALES' />
          </named-connection>
        </named-connections>
        <relation connection='snowflake.bare1' name='ORDERS' table='[ORDERS]' type='table' />
      </connection>
    </datasource>
  </datasources>
</workbook>
"""
)

TWB_PUBLISHED = (
    _HEADER
    + """
<workbook version='18.1'>
  <datasources>
    <datasource name='Parameters'>
      <column caption='Top N' datatype='integer' name='[Top N]' />
    </datasource>
    <datasource caption='Published Orders' name='sqlproxy.pub0'>
      <repository-location id='PublishedOrders' path='/datasources' site='acme' />
      <connection class='sqlproxy' dbname='PublishedOrders' server='tableau.acme.com' />
    </datasource>
  </datasources>
</workbook>
"""
)

TWB_LEGACY_DIRECT = (
    _HEADER
    + """
<workbook version='9.3'>
  <datasources>
    <datasource caption='Old Style' name='snowflake.legacy0'>
      <connection class='snowflake' dbname='LEGACY_DB' schema='SALES'>
        <relation name='ORDERS' table='[SALES].[ORDERS]' type='table' />
      </connection>
    </datasource>
  </datasources>
</workbook>
"""
)

TWB_UNION = (
    _HEADER
    + """
<workbook version='18.1'>
  <datasources>
    <datasource caption='Unioned Orders' name='federated.uni0'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='acme' name='snowflake.uni1'>
            <connection class='snowflake' dbname='LEGACY_DB' schema='SALES' />
          </named-connection>
        </named-connections>
        <relation name='unioned' type='union'>
          <relation connection='snowflake.uni1' name='ORDERS_2024'
            table='[SALES].[ORDERS_2024]' type='table' />
          <relation connection='snowflake.uni1' name='ORDERS_2025'
            table='[SALES].[ORDERS_2025]' type='table' />
        </relation>
      </connection>
    </datasource>
  </datasources>
</workbook>
"""
)


def test_two_tables_extracts_both_relations(tmp_path):
    relations = extract_relations(write_twb(tmp_path, TWB_TWO_TABLES))
    assert len(relations) == 2
    assert all(r.kind == "table" for r in relations)
    assert [r.fqn for r in relations] == [
        "LEGACY_DB.SALES.ORDERS",
        "LEGACY_DB.CRM.CUSTOMERS",
    ]
    assert all(r.connection_class == "snowflake" for r in relations)
    assert all(r.has_extract is False for r in relations)
    assert [r.datasource for r in relations] == ["Orders (Legacy)", "Customers (Legacy)"]


def test_two_tables_field_breakdown(tmp_path):
    orders = extract_relations(write_twb(tmp_path, TWB_TWO_TABLES))[0]
    assert orders.database == "LEGACY_DB"
    assert orders.schema == "SALES"
    assert orders.table == "ORDERS"
    assert orders.custom_sql is None
    assert orders.refusal_reason is None


def test_custom_sql_carried_verbatim(tmp_path):
    relations = extract_relations(write_twb(tmp_path, TWB_CUSTOM_SQL))
    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "custom_sql"
    assert rel.datasource == "Daily KPI (Custom SQL)"
    assert rel.custom_sql is not None
    assert rel.custom_sql.startswith("SELECT ORDER_DATE, SUM(SALES) AS TOTAL_SALES")
    assert rel.custom_sql.endswith("GROUP BY ORDER_DATE")
    assert rel.fqn is None


def test_join_refused_whole_without_nested_tables(tmp_path):
    relations = extract_relations(write_twb(tmp_path, TWB_JOIN))
    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "refused"
    assert rel.refusal_reason == REFUSAL_JOIN == "join"
    assert rel.datasource == "Orders + Customers (Join)"
    assert [r for r in relations if r.kind == "table"] == []


def test_union_refused_whole_without_nested_tables(tmp_path):
    relations = extract_relations(write_twb(tmp_path, TWB_UNION))
    assert len(relations) == 1
    assert relations[0].kind == "refused"
    assert relations[0].refusal_reason == REFUSAL_UNION == "union"
    assert [r for r in relations if r.kind == "table"] == []


def test_extract_only_datasource_refused(tmp_path):
    relations = extract_relations(write_twb(tmp_path, TWB_EXTRACT_ONLY))
    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "refused"
    assert rel.refusal_reason == REFUSAL_EXTRACT_ONLY == "extract-only"
    assert rel.datasource == "Offline Extract"


def test_extract_over_live_relation_stays_eligible(tmp_path):
    relations = extract_relations(write_twb(tmp_path, TWB_EXTRACT_OVER_LIVE))
    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "table"
    assert rel.fqn == "LEGACY_DB.SALES.ORDERS"
    assert rel.has_extract is True
    assert rel.connection_class == "snowflake"


def test_malformed_workbook_raises_parse_error(tmp_path):
    with pytest.raises(TableauParseError):
        extract_relations(write_twb(tmp_path, TWB_MALFORMED))


def test_bare_table_attr_falls_back_to_connection_schema(tmp_path):
    relations = extract_relations(write_twb(tmp_path, TWB_BARE_TABLE))
    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "table"
    assert rel.database == "LEGACY_DB"
    assert rel.schema == "SALES"
    assert rel.table == "ORDERS"
    assert rel.fqn == "LEGACY_DB.SALES.ORDERS"


def test_published_datasource_refused_and_parameters_skipped(tmp_path):
    relations = extract_relations(write_twb(tmp_path, TWB_PUBLISHED))
    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "refused"
    assert rel.refusal_reason == REFUSAL_PUBLISHED == "published"
    assert rel.datasource == "Published Orders"


def test_legacy_direct_connection_shape(tmp_path):
    relations = extract_relations(write_twb(tmp_path, TWB_LEGACY_DIRECT))
    assert len(relations) == 1
    rel = relations[0]
    assert rel.kind == "table"
    assert rel.fqn == "LEGACY_DB.SALES.ORDERS"
    assert rel.connection_class == "snowflake"
    assert rel.has_extract is False


def test_missing_workbook_raises_parse_error(tmp_path):
    with pytest.raises(TableauParseError):
        extract_relations(tmp_path / "does-not-exist.twb")

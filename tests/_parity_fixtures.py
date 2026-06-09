"""Workbook fixtures for the migration parity family (PARITY-PLAN S1.2).

Five cases, all loadable through plumb.checks._tableau.read_twb_xml /
parse_workbook (the single workbook byte loader):

  TWB_TWO_TABLES     two single-table Snowflake relations (eligible)
  TWB_CUSTOM_SQL     one custom-SQL relation (eligible, snapshotted verbatim)
  TWB_JOIN           a join relation (refused: join)
  TWB_EXTRACT_ONLY   a .hyper-only datasource, no live relation (refused)
  TWB_MALFORMED      truncated XML (parse error path)

The XML shapes mirror real Tableau 2024+ federated datasources: a
<connection class='federated'> holding <named-connections> plus one or more
<relation> elements. Tests write these to tmp_path as .twb files via
write_twb().
"""

from __future__ import annotations

from pathlib import Path

_HEADER = "<?xml version='1.0' encoding='utf-8' ?>\n"

TWB_TWO_TABLES = (
    _HEADER
    + """
<workbook version='18.1'>
  <datasources>
    <datasource caption='Orders (Legacy)' name='federated.0aaa111' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='acme.snowflakecomputing.com' name='snowflake.1bbb222'>
            <connection class='snowflake' dbname='LEGACY_DB' schema='SALES'
              server='acme.snowflakecomputing.com' warehouse='ANALYST_WH' />
          </named-connection>
        </named-connections>
        <relation connection='snowflake.1bbb222' name='ORDERS'
          table='[SALES].[ORDERS]' type='table' />
      </connection>
      <column caption='Order Date' datatype='date' name='[ORDER_DATE]' role='dimension' />
      <column caption='Sales' datatype='real' name='[SALES]' role='measure' />
    </datasource>
    <datasource caption='Customers (Legacy)' name='federated.2ccc333' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='acme.snowflakecomputing.com' name='snowflake.3ddd444'>
            <connection class='snowflake' dbname='LEGACY_DB' schema='CRM'
              server='acme.snowflakecomputing.com' warehouse='ANALYST_WH' />
          </named-connection>
        </named-connections>
        <relation connection='snowflake.3ddd444' name='CUSTOMERS'
          table='[CRM].[CUSTOMERS]' type='table' />
      </connection>
      <column caption='Customer Id' datatype='string' name='[CUSTOMER_ID]' role='dimension' />
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name='Sales by Region'>
      <table>
        <view>
          <datasource-dependencies datasource='federated.0aaa111'>
            <column datatype='real' name='[SALES]' role='measure' />
          </datasource-dependencies>
        </view>
      </table>
    </worksheet>
  </worksheets>
</workbook>
"""
)

TWB_CUSTOM_SQL = (
    _HEADER
    + """
<workbook version='18.1'>
  <datasources>
    <datasource caption='Daily KPI (Custom SQL)' name='federated.4eee555' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='acme.snowflakecomputing.com' name='snowflake.5fff666'>
            <connection class='snowflake' dbname='LEGACY_DB' schema='SALES'
              server='acme.snowflakecomputing.com' warehouse='ANALYST_WH' />
          </named-connection>
        </named-connections>
        <relation connection='snowflake.5fff666' name='Custom SQL Query'
          type='text'>SELECT ORDER_DATE, SUM(SALES) AS TOTAL_SALES
FROM LEGACY_DB.SALES.ORDERS GROUP BY ORDER_DATE</relation>
      </connection>
    </datasource>
  </datasources>
  <worksheets />
</workbook>
"""
)

TWB_JOIN = (
    _HEADER
    + """
<workbook version='18.1'>
  <datasources>
    <datasource caption='Orders + Customers (Join)' name='federated.6ggg777' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='acme.snowflakecomputing.com' name='snowflake.7hhh888'>
            <connection class='snowflake' dbname='LEGACY_DB' schema='SALES'
              server='acme.snowflakecomputing.com' warehouse='ANALYST_WH' />
          </named-connection>
        </named-connections>
        <relation join='inner' type='join'>
          <clause type='join'>
            <expression op='='>
              <expression op='[ORDERS].[CUSTOMER_ID]' />
              <expression op='[CUSTOMERS].[CUSTOMER_ID]' />
            </expression>
          </clause>
          <relation connection='snowflake.7hhh888' name='ORDERS'
            table='[SALES].[ORDERS]' type='table' />
          <relation connection='snowflake.7hhh888' name='CUSTOMERS'
            table='[CRM].[CUSTOMERS]' type='table' />
        </relation>
      </connection>
    </datasource>
  </datasources>
  <worksheets />
</workbook>
"""
)

TWB_EXTRACT_ONLY = (
    _HEADER
    + """
<workbook version='18.1'>
  <datasources>
    <datasource caption='Offline Extract' name='federated.8iii999' version='18.1'>
      <connection class='hyper' dbname='Data/Extracts/offline.hyper' />
      <extract count='-1' enabled='true' units='records'>
        <connection class='hyper' dbname='Data/Extracts/offline.hyper' />
      </extract>
      <column caption='Amount' datatype='real' name='[AMOUNT]' role='measure' />
    </datasource>
  </datasources>
  <worksheets />
</workbook>
"""
)

# An extract OVER a live snowflake relation: eligible (parity runs against
# the warehouse objects the extract refreshes from), has_extract flagged.
TWB_EXTRACT_OVER_LIVE = (
    _HEADER
    + """
<workbook version='18.1'>
  <datasources>
    <datasource caption='Orders (Extracted)' name='federated.9jjj000' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='acme.snowflakecomputing.com' name='snowflake.0kkk111'>
            <connection class='snowflake' dbname='LEGACY_DB' schema='SALES'
              server='acme.snowflakecomputing.com' warehouse='ANALYST_WH' />
          </named-connection>
        </named-connections>
        <relation connection='snowflake.0kkk111' name='ORDERS'
          table='[SALES].[ORDERS]' type='table' />
      </connection>
      <extract count='-1' enabled='true' units='records'>
        <connection class='hyper' dbname='Data/Extracts/orders.hyper' />
      </extract>
    </datasource>
  </datasources>
  <worksheets />
</workbook>
"""
)

TWB_MALFORMED = _HEADER + "<workbook version='18.1'><datasources><datasource"


def write_twb(tmp_path: Path, content: str, name: str = "fixture.twb") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path

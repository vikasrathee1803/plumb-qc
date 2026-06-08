-- Plumb least-privilege Snowflake setup.
--
-- Run once by an administrator. This creates a dedicated read-only role and a
-- dedicated warehouse for Plumb. Plumb is read-only by design and its engine
-- refuses any non-read, but the authoritative control in a regulated setup is
-- this RBAC grant: with SELECT-only privileges, a write is impossible at the
-- database, independent of the application.
--
-- Replace <DB>, <SCHEMA>, and <ANALYST_USER> before running. Repeat the schema
-- grants for each schema Plumb should be allowed to read.

-- 1. A read-only role.
USE ROLE SECURITYADMIN;
CREATE ROLE IF NOT EXISTS PLUMB_QC
  COMMENT = 'Read-only role for Plumb QC. SELECT only; no write/DDL/DML.';

-- 2. A small, auto-suspending warehouse so Plumb's reads are isolated and cost
--    is bounded. Plumb also sets a statement timeout and a fetched-row cap.
USE ROLE SYSADMIN;
CREATE WAREHOUSE IF NOT EXISTS PLUMB_WH
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Dedicated warehouse for Plumb QC reads.';
GRANT USAGE ON WAREHOUSE PLUMB_WH TO ROLE PLUMB_QC;

-- 3. SELECT-only grants on the schemas Plumb may read. Repeat per schema.
--    INFORMATION_SCHEMA (used by metadata checks) is available with USAGE on
--    the database, so no extra grant is needed for it.
USE ROLE SECURITYADMIN;
GRANT USAGE ON DATABASE <DB> TO ROLE PLUMB_QC;
GRANT USAGE ON SCHEMA <DB>.<SCHEMA> TO ROLE PLUMB_QC;
GRANT SELECT ON ALL TABLES IN SCHEMA <DB>.<SCHEMA> TO ROLE PLUMB_QC;
GRANT SELECT ON FUTURE TABLES IN SCHEMA <DB>.<SCHEMA> TO ROLE PLUMB_QC;
GRANT SELECT ON ALL VIEWS IN SCHEMA <DB>.<SCHEMA> TO ROLE PLUMB_QC;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA <DB>.<SCHEMA> TO ROLE PLUMB_QC;

-- 4. Assign the role to each analyst (key-pair or SSO user). Plumb connects
--    with role = PLUMB_QC and warehouse = PLUMB_WH in ~/.plumb/connection.yml.
GRANT ROLE PLUMB_QC TO USER <ANALYST_USER>;

-- 5. Optional: enable AI assist (Snowflake Cortex). It runs in-database, so no
--    data leaves Snowflake and no external API key is needed. The role needs
--    the Cortex usage privilege:
-- USE ROLE ACCOUNTADMIN;
-- GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE PLUMB_QC;

-- Do NOT run Plumb with ACCOUNTADMIN, SECURITYADMIN, SYSADMIN, or ORGADMIN.
-- Plumb warns when a connection profile uses one of these roles.

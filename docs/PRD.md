# PRD (thin layer over PLUMB_SPEC.md)

The spec is authoritative. This file holds only the product framing the
delivery team works from day to day.

## Problem

Analysts ship Snowflake SQL and Tableau workbooks that look right and are
wrong: join fan-outs inflate totals, grain errors double count, values
drift silently against source of truth. Corrections after publish burn
trust and reviewer time.

## Product integrity contract: coverage

The single most important product rule: a green verdict that skipped the
checks that mattered is a product failure. Coverage is always computed,
always reported, and ranked by risk (ADR-0002). No surface may render a
verdict without its coverage qualifier.

## Success measures

- Post-publish data correction rate drops.
- Review cycle time drops because reviewers trust a green Plumb report.
- An analyst catches a fan-out, grain error, or value drift on their own
  machine in minutes.

## Assumptions status (confirmed 2026-06-07, defaults in force)

1. Git and PRs exist for some SQL: build CLI-first, CI gate additive. OK.
2. Tableau Metadata API: Phase 3 only, no current dependency. OK.
3. PLUMB_QC_ROLE and PLUMB_WH provisioning: pending with the user's
   Snowflake admin, needed before Gate 1 live verification. OPEN.
4. AI assist wanted as strict assistant only: ai/ layer is omissible with
   zero verdict impact. OK.

## Non-goals

Scheduling, orchestration, catalog or lineage ownership, any write to
Snowflake or Tableau, application unit testing.

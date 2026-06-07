# Plumb: BI Build QC and Confidence Engine

**Codename:** Plumb (a plumb line tests whether something is true and sound). Rename freely.

**One line:** A local-first, centrally governed tool that lets a BI analyst prove a SQL build or a Tableau workbook is correct before it ships, producing a shareable confidence report.

**Owner:** Business Analytics and Insights. **Audience:** a BI team of roughly 50 in an enterprise Snowflake plus Tableau environment.

---

## How to use this document with Claude Code

Paste the kickoff prompt below into Claude Code, then point it at this file. Build strictly in the phase order in the Build Sequence section. Do not start Phase 2 until every Phase 1 acceptance criterion passes.

### Kickoff prompt (paste into Claude Code)

```
You are a senior Python engineer building "Plumb", a local-first QC tool for a
Snowflake plus Tableau BI team. The full specification is in PLUMB_SPEC.md in
this directory. Read it fully before writing any code.

Build Phase 0 then Phase 1 only. Stop after Phase 1 and report what you built
against the Phase 1 acceptance criteria.

Hard rules:
- Deterministic checks decide pass or fail. The AI assist layer never decides a verdict.
- No secrets in source. Snowflake auth is key-pair, SSO (externalbrowser), or OAuth only.
- Every Snowflake query the tool issues sets QUERY_TAG and runs on the dedicated
  warehouse with a statement timeout and a result row cap.
- Validate all config with pydantic. Fail loudly on a malformed ruleset.
- No em dashes anywhere in code comments, docs, or generated reports.
- Write tests for the verdict logic and at least one check per family.

Use the exact stack, folder structure, data contracts, and CLI surface defined
in the spec. Ask no clarifying questions that the spec already answers.
```

---

## Outcome

- **Who this is for:** BI analysts and data engineers who write Snowflake SQL and build Tableau workbooks, plus the leads who review their work.
- **Job to be done:** before publishing, verify the build is correct and get evidence I can show a reviewer or attach to a pull request.
- **What success looks like:** the rate of post-publish data corrections drops, review cycles get shorter because reviewers trust a green Plumb report, and analysts catch fan-outs, grain errors, and silent value drift on their own machine in minutes.
- **What failure looks like:** an analyst trusts a green result that did not actually run the checks that mattered, or the tool is so slow or fiddly that nobody runs it. Both are designed against directly (see the Coverage model and the local-first, single-command design).

---

## Scope and phasing

### Must have (Phase 1, blocks launch)
- SQL static analysis, schema and metadata checks, execution-based data assertions, regression diff against a saved baseline, and basic performance and cost checks, all against Snowflake.
- A tiered confidence verdict with a coverage indicator.
- A self-contained HTML report, plus JSON and JUnit XML output.
- A versioned, pydantic-validated YAML ruleset, pinnable to a central repo.
- Secure Snowflake auth, query tagging, and warehouse and timeout guardrails.
- A `plumb` CLI with deterministic exit codes for CI.

### Should have (Phase 2, ships if Phase 1 is solid)
- Tableau workbook static analysis from a `.twbx` or `.twb` file.
- A local interactive web UI (FastAPI plus a small React SPA) wrapping the same engine.
- Shared team baselines stored in a Snowflake stage or object store.
- Optional AI assist: explain a failure, draft a fix, draft reconciliation SQL from plain English.

### Phase 3 (explicit deferral, do not build until 1 and 2 are adopted)
- Tableau live reconciliation: compare a dashboard's summary export to an equivalent Snowflake query.
- Tableau Metadata API lineage checks and cross-workbook metric consistency.

### Won't have (explicit non-goals)
- A scheduler or orchestration engine. Plumb runs on demand locally and in CI. Use existing orchestration for scheduled monitoring.
- A data catalog or lineage product. Plumb consumes governance metadata, it does not own it.
- Write access to Snowflake or to Tableau Server. Plumb is read-only everywhere.
- A replacement for unit testing of application code. This is BI build QC.

---

## Architecture Decision Record

### Stack
- **Language:** Python 3.11 or later.
- **SQL parsing and dialect:** sqlglot (parse, transpile, dialect = snowflake).
- **SQL style lint:** sqlfluff (dialect = snowflake), driven by the org ruleset.
- **Snowflake access:** snowflake-connector-python. Auth: key-pair, externalbrowser SSO, or OAuth. Never password in config.
- **Config and contracts:** YAML rulesets validated with pydantic models.
- **CLI:** Typer, with Rich for the terminal report.
- **Reporting:** Jinja2 to a single self-contained HTML file (inline CSS, no external assets), plus JSON, plus JUnit XML.
- **Baselines:** local Parquet plus a manifest JSON by default; optional shared Snowflake stage or object store in Phase 2.
- **Web UI (Phase 2):** FastAPI backend, Vite plus React SPA.
- **AI assist (Phase 2, optional):** Anthropic Python SDK, API key server-side or in the OS keychain, never in the repo.
- **Packaging:** pip and pipx installable internal package, pinned dependencies, plus a Dockerfile for CI.

### Folder structure
```
plumb/
  pyproject.toml
  README.md
  Dockerfile                 # for CI runners
  plumb/
    __init__.py
    cli.py                   # Typer entrypoint, exit codes
    config/
      models.py              # pydantic models: Ruleset, CheckSpec, Profile, ConnectionProfile
      loader.py              # load, validate, resolve profile, pin version
    connect/
      snowflake.py           # auth, session, query_tag, statement_timeout, row cap, dedicated warehouse
    engine/
      runner.py              # orchestrates a run, collects CheckResults
      verdict.py             # severity gates plus coverage model
      registry.py            # check registration and plugin seam
      models.py              # CheckResult, RunResult dataclasses or pydantic
    checks/
      sql_static.py          # sqlglot and sqlfluff based checks
      sql_meta.py            # INFORMATION_SCHEMA checks
      sql_assertions.py      # grain, null, RI, domain, range, freshness, recon, dup, additivity
      sql_regression.py      # baseline diff
      sql_performance.py     # explain, profile, cost, cardinality
      tableau_static.py      # Phase 2
      tableau_live.py        # Phase 3
    baseline/
      store.py               # save and load golden result sets, manifest, fingerprints
    report/
      templates/report.html.j2
      html.py
      json_out.py
      junit.py
    ai/                      # Phase 2, optional
      client.py              # Anthropic SDK wrapper
      explain.py
      fix.py
      recon_sql.py
  rules/                     # default ruleset; teams fork this into a central plumb-rules repo
    plumb.yml
    profiles/
      finance.yml
      marketing.yml
  web/                       # Phase 2
    api/
    ui/
  tests/
    test_verdict.py
    test_checks_*.py
    fixtures/
```

### Approved packages
sqlglot, sqlfluff, snowflake-connector-python, pydantic (v2), PyYAML, typer, rich, jinja2, pyarrow (Parquet baselines), python-dotenv (local dev only), keyring (secret storage). Phase 2 adds fastapi, uvicorn, anthropic, and a React or Vite frontend. Tableau static analysis uses tableaudocumentapi plus lxml; Phase 3 adds tableauserverclient for the Metadata API.

### Forbidden patterns
- No secrets, keys, or passwords in source or in the ruleset repo.
- No write or DDL or DML statements issued to Snowflake, ever. The engine refuses anything that is not a read.
- No check may decide its status from an LLM response. Statuses are deterministic.
- No silent network egress of raw data rows. Evidence samples are capped and PII-redacted by default.
- No bare `except`. No `print` for user output; use Rich or the logger.
- No unpinned dependencies in the shipped package.

### Security and governance
- **Role:** a dedicated `PLUMB_QC_ROLE` with `SELECT` on the schemas in scope plus `USAGE` on `INFORMATION_SCHEMA`. Read on `SNOWFLAKE.ACCOUNT_USAGE` only if account-level checks are enabled (note its latency can be hours; prefer `INFORMATION_SCHEMA` for real-time freshness and existence checks).
- **Warehouse:** a dedicated `PLUMB_WH` sized XS with auto-suspend at 60 seconds, so QC load is isolated and cheap.
- **Auth:** key-pair preferred for CI and headless use, externalbrowser SSO for interactive analysts, OAuth where the org standardizes on it. Secrets via OS keychain (keyring) or environment, never the repo.
- **Cost control on every query:** set `QUERY_TAG = 'plumb_qc:{run_id}'`, a session `STATEMENT_TIMEOUT_IN_SECONDS`, and a hard result row cap from the ruleset. This makes QC spend trivial to attribute in `QUERY_HISTORY` and prevents a runaway check from scanning a fact table.
- **Data handling:** results stay on the analyst's machine by default. Evidence sample rows are capped (default 20) and run through PII redaction (configurable column patterns and types). A `--aggregate-only` flag suppresses row samples entirely for sensitive domains.
- **Audit:** every run writes a JSON-lines audit record (who, when, target, ruleset version, verdict) locally, with an optional central sink in Phase 2.
- **Governance distribution:** the org maintains a versioned `plumb-rules` git repo. The local tool pins a ruleset version (`plumb rules pin <version>`), so all 50 analysts check against the same standard, and standards change through a reviewed commit, not by individual edits.

### Deployment target
- Distributed as an internal pip or pipx package, version pinned. `plumb init` scaffolds a connection profile and a sample check spec. The same package runs in CI via the Dockerfile, gated on the CLI exit code.

### Out of scope for the architecture
Scheduling, alerting, multi-tenant hosting, a permissions system beyond Snowflake roles, and any persistence layer beyond local files and the optional Phase 2 shared baseline store.

---

## The Confidence Verdict model

The headline is a tiered verdict, never a single percentage. Each check has a severity: `BLOCKER`, `HIGH`, `MEDIUM`, `LOW`, `INFO`.

Verdict logic:
- Any `BLOCKER` fails: **BLOCKED**.
- No blocker, any `HIGH` fails: **REVIEW**.
- No blocker or high, only `MEDIUM` or `LOW` issues: **READY_WITH_NOTES**.
- Nothing fails: **READY**.

Coverage is reported alongside the verdict and is the honesty mechanism. It lists which check families ran and which were skipped and why. A clean run that skipped reconciliation and had no baseline is shown as `READY (limited coverage: reconciliation skipped, no baseline)`, not as unqualified green. The report ranks coverage gaps so the analyst sees the most important unchecked risk first.

An internal numeric score may be computed for trend reporting over time, but it is never the headline and never overrides the gates.

---

## Check catalog

Severities below are defaults; the ruleset can override any of them per check or per profile. Each check declares whether it is static (no execution), metadata (reads `INFORMATION_SCHEMA`), or execution (runs read-only SQL against the target).

### SQL static analysis (sqlglot, sqlfluff)
| ID | Check | How | Default severity |
|---|---|---|---|
| S-LINT-001 | Style and convention lint against org rules | static | LOW |
| S-STAT-001 | `SELECT *` in a production query | static | HIGH |
| S-STAT-002 | Cross or cartesian join with no join condition | static | BLOCKER |
| S-STAT-003 | `NOT IN` with a subquery (NULL trap); suggest `NOT EXISTS` | static | HIGH |
| S-STAT-004 | Implicit type cast inside a join or filter predicate | static | MEDIUM |
| S-STAT-005 | Non-SARGable predicate (function wrapped around a filtered column) | static | LOW |
| S-STAT-006 | Hardcoded literal, magic number, or hardcoded date | static | MEDIUM |
| S-STAT-007 | Reference to a deprecated or blocklisted object | static + metadata | HIGH |
| S-STAT-008 | Ambiguous or implicit join type | static | MEDIUM |
| S-STAT-010 | `DISTINCT` used to mask a likely join fan-out (heuristic) | static | MEDIUM |

### SQL schema and metadata (INFORMATION_SCHEMA)
| ID | Check | How | Default severity |
|---|---|---|---|
| S-META-001 | All referenced tables and columns exist | metadata | BLOCKER |
| S-META-002 | Join key data types are compatible | metadata | HIGH |
| S-META-003 | Referenced objects are not flagged deprecated | metadata | HIGH |
| S-META-004 | Source is a certified or approved object | metadata | MEDIUM |

### SQL data assertions (execution, read-only)
| ID | Check | How | Default severity |
|---|---|---|---|
| D-GRAIN-001 | Declared key is unique (no duplicates); catches fan-out | execution | BLOCKER |
| D-GRAIN-002 | Row count within expected bounds (absolute or vs baseline tolerance) | execution | HIGH |
| D-NULL-001 | Declared key columns are not null | execution | BLOCKER |
| D-NULL-002 | Null rate on declared columns within threshold | execution | MEDIUM |
| D-RI-001 | Referential integrity: no orphan foreign keys | execution | HIGH |
| D-DOMAIN-001 | Values fall within an allowed set or enum | execution | MEDIUM |
| D-RANGE-001 | Numeric or date values within expected bounds | execution | MEDIUM |
| D-FRESH-001 | Freshness: max event timestamp within SLA | execution | HIGH |
| D-RECON-001 | Aggregates tie to a source-of-truth query within tolerance | execution | BLOCKER |
| D-DUP-001 | Full-row duplicate detection | execution | MEDIUM |
| D-ADD-001 | Non-additive measure guard (flag SUM over a ratio or average) | static + execution | MEDIUM |

### SQL regression and diff (execution plus baseline)
| ID | Check | How | Default severity |
|---|---|---|---|
| R-DIFF-001 | Result-set diff vs saved golden baseline: schema, row-level, and aggregate changes classified as added, removed, changed, within tolerance | execution | HIGH |
| R-AGG-001 | Aggregate fingerprint diff (cheaper signal for large result sets) | execution | MEDIUM |

### SQL performance and cost (EXPLAIN, query profile)
| ID | Check | How | Default severity |
|---|---|---|---|
| P-PROF-001 | Plan analysis: full table scans, exploding joins, weak pruning | execution | LOW |
| P-COST-001 | Estimated bytes or partitions scanned vs threshold | execution | LOW |
| P-SPILL-001 | Query profile shows spillage to local or remote disk | execution | LOW |
| P-CARD-001 | Intermediate cardinality explosion detected | execution | MEDIUM |

### Tableau static analysis (Phase 2, parse `.twbx` or `.twb`)
| ID | Check | How | Default severity |
|---|---|---|---|
| T-SRC-001 | Custom SQL present; recommend a certified view or published source | static | MEDIUM |
| T-SRC-002 | Live vs extract; extract refresh staleness | static | MEDIUM |
| T-SRC-003 | Uses a certified or published data source | static | HIGH |
| T-LOD-001 | FIXED LOD inventory and double-count risk flags | static | HIGH |
| T-CALC-001 | Aggregation inside a calc that may mismatch DB grain | static | MEDIUM |
| T-CALC-002 | Hardcoded values in calcs or filters | static | MEDIUM |
| T-NAME-001 | Field and data source naming conventions | static | LOW |
| T-UNUSED-001 | Unused fields, data sources, or sheets | static | LOW |
| T-FMT-001 | Number and date format consistency across sheets | static | LOW |
| T-FILT-001 | Quick filter count and "only relevant values" performance smells | static | LOW |
| T-RLS-001 | Row-level security calc present where the profile requires it | static | HIGH |
| T-TOTAL-001 | Grand totals applied to a non-additive measure | static | MEDIUM |

### Tableau live and lineage (Phase 3)
| ID | Check | How | Default severity |
|---|---|---|---|
| T-RECON-001 | Dashboard summary export reconciles to an equivalent Snowflake query within tolerance | semi-automated, then Metadata API | BLOCKER |
| T-LINEAGE-001 | Dashboard fields trace to non-deprecated upstream objects | Metadata API | HIGH |
| T-CONSIST-001 | Same KPI shows the same number across workbooks | Metadata API | HIGH |

---

## Data contracts

### Connection profile (local, not in the rules repo)
```yaml
account: "myorg-account"
user: "VIKAS"
authenticator: "snowflake_jwt"   # or "externalbrowser" or "oauth"
private_key_path: "~/.plumb/keys/plumb_rsa_key.p8"   # for key-pair
role: "PLUMB_QC_ROLE"
warehouse: "PLUMB_WH"
```

### Ruleset (versioned, central; pinned by the local tool)
```yaml
version: "2026.06.0"
defaults:
  fail_on: "REVIEW"            # CI gate: READY_WITH_NOTES | REVIEW | BLOCKED
  max_result_rows: 100000
  statement_timeout_s: 120
  evidence_sample_rows: 20
  redact_pii: true
naming:
  table_regex: "^(dim|fct|stg|rpt)_[a-z0-9_]+$"
  tableau_field_regex: "^[A-Z][A-Za-z0-9 ]+$"
deprecated_objects:
  - "ANALYTICS.LEGACY.V_OLD_SALES"
certified_sources:
  - "ANALYTICS.MART.FCT_SALES"
severity_overrides:
  S-STAT-001: HIGH
thresholds:
  null_rate_default: 0.0
  freshness_sla_hours_default: 24
checks:
  - id: D-GRAIN-001
    enabled: true
    params: { key: ["order_id"] }
  - id: D-RECON-001
    enabled: true
    params:
      metric_sql: "SELECT SUM(amount) FROM {{ target }}"
      source_of_truth_sql: "SELECT SUM(net_amount) FROM ANALYTICS.MART.FCT_SALES"
      tolerance_abs: 0
      tolerance_pct: 0.001
  - id: D-FRESH-001
    enabled: true
    params: { event_ts_col: "created_at", sla_hours: 6 }
```

A `profile` (for example `finance.yml`) inherits from the base ruleset and overrides thresholds, severities, and the enabled check set. The analyst selects a profile per run.

### Run result (JSON, the machine-readable contract)
```json
{
  "run_id": "uuid",
  "timestamp": "ISO8601",
  "target": { "type": "sql", "name": "rpt_daily_sales", "source_ref": "queries/rpt_daily_sales.sql" },
  "ruleset_version": "2026.06.0",
  "profile": "finance",
  "verdict": "BLOCKED",
  "coverage": {
    "families_run": ["static", "metadata", "assertions", "performance"],
    "families_skipped": [{ "family": "regression", "reason": "no baseline found" }]
  },
  "summary": { "blocker": 1, "high": 0, "medium": 2, "low": 3, "info": 4, "passed": 19, "total": 29 },
  "checks": [
    {
      "id": "D-GRAIN-001",
      "name": "Grain uniqueness on declared key",
      "family": "assertions",
      "severity": "BLOCKER",
      "status": "FAIL",
      "observed": "12 duplicate key groups, max duplication 4x",
      "expected": "0 duplicates on [order_id]",
      "evidence": { "query": "SELECT ...", "sample_rows": [] },
      "remediation": "A join to dim_customer is fanning out. Aggregate to grain or correct the join key.",
      "ai_explanation": null
    }
  ],
  "environment": { "warehouse": "PLUMB_WH", "role": "PLUMB_QC_ROLE", "query_tag": "plumb_qc:uuid" }
}
```

`status` is one of `PASS`, `FAIL`, `WARN`, `SKIP`, `ERROR`. `WARN` is used when a check runs but cannot fully assert (for example, a heuristic flag). `ERROR` means the check itself failed to run and is surfaced separately so it never silently counts as a pass.

---

## CLI surface

```
plumb init                                   # scaffold connection profile and a sample spec
plumb rules pull                             # fetch the central ruleset repo
plumb rules pin <version>                    # pin the active ruleset version
plumb check sql --query path.sql --profile finance [--baseline name] [--explain]
plumb check sql --inline "SELECT ..." --profile marketing
plumb check tableau --workbook path.twbx --profile finance     # Phase 2
plumb baseline create --query path.sql --name sales_daily
plumb baseline update --name sales_daily
plumb report open                            # open the most recent HTML report
```

Exit codes (the CI gate reads these):
- `0` verdict at or above `fail_on` threshold (passing).
- `1` REVIEW (configurable in the ruleset whether this fails CI).
- `2` BLOCKED.
- `3` tool or connection error.

`--explain` (Phase 2) attaches AI explanations to failing checks. It never changes a status.

---

## Optional AI assist layer (Phase 2)

The assist layer is opt-in, runs only on already-decided results, and is forbidden from setting any status. Three functions, each with a strict JSON contract and grounding rules.

### Explain a failure
```
You are a senior analytics engineer reviewing a single failed QC check.

You are given: the check id and name, the SQL or workbook context, the observed
versus expected result, and any evidence sample. You explain, in plain business
English, why this likely failed and what it means for the numbers.

Rules:
- Use only the information provided. Never invent table names, values, or counts.
- If the input is insufficient to explain the failure, say so plainly in the field.
- Do not restate the check definition. Explain the likely root cause.
- 2 to 4 sentences. No jargon the analyst would not use.

Output ONLY valid JSON, no markdown:
{
  "root_cause": "string, 1 to 2 sentences",
  "business_impact": "string, 1 to 2 sentences",
  "confidence": "high | medium | low"
}

Example:
{
  "root_cause": "The join from orders to the customer dimension is at a finer grain than orders, so each order is duplicated once per matching customer row.",
  "business_impact": "Total revenue is overstated by roughly 4x for affected orders, which would inflate any dashboard summing this field.",
  "confidence": "high"
}
```

### Draft a fix
```
You are a senior analytics engineer proposing a minimal fix for a failed QC check.

Rules:
- Propose the smallest change that resolves the specific failure. Do not rewrite
  the whole query.
- Only reference objects and columns present in the provided context.
- If a fix cannot be determined safely, return null for the patch and explain why.

Output ONLY valid JSON, no markdown:
{
  "explanation": "string, 1 to 2 sentences on what the fix does",
  "patch": "string SQL snippet or null",
  "needs_human_review": true
}
```

### Draft reconciliation SQL from plain English
```
You convert a plain-English reconciliation intent into a Snowflake SQL query that
returns a single comparable aggregate, for use as a source-of-truth check.

Rules:
- Use only the objects and columns the analyst names. Never guess a schema.
- Return one scalar aggregate. No SELECT *.
- If the intent is ambiguous about grain or filter, return null and list the
  specific question that must be answered first.

Output ONLY valid JSON, no markdown:
{
  "sql": "string or null",
  "assumptions": ["string"],
  "blocking_question": "string or null"
}
```

All three are called with `max_tokens` tuned to the task (explanation around 300, fix around 500). The parser strips code fences and falls back to extracting the first JSON object. A parse failure degrades gracefully: the check still shows its deterministic status, with the AI field left null.

---

## Acceptance criteria

### Phase 1 (binary, all must pass)
- [ ] `plumb check sql --query f.sql --profile finance` runs every enabled SQL check and exits with the correct code for the resulting verdict.
- [ ] A query with a join that multiplies rows produces a `BLOCKED` verdict via `D-GRAIN-001`, and the HTML report names the duplicated key and shows a capped, PII-redacted sample.
- [ ] A reconciliation check that is off by more than tolerance produces `BLOCKED` via `D-RECON-001` and reports observed versus expected with the difference.
- [ ] Running against a saved baseline, an unchanged query reports `R-DIFF-001` as PASS, and a query whose output changed reports the specific rows or aggregates that moved.
- [ ] A clean run that had no baseline and skipped reconciliation reports `READY` with coverage explicitly listing both skips and ranking them.
- [ ] Every Snowflake query issued by the tool carries the `plumb_qc:{run_id}` query tag, runs on `PLUMB_WH`, and respects the statement timeout and row cap (verified in `QUERY_HISTORY`).
- [ ] A malformed ruleset fails with a clear pydantic validation message and a non-zero exit, and never runs partial checks silently.
- [ ] The tool refuses to execute any statement that is not a read.
- [ ] Output is produced in all three formats: a self-contained HTML file, JSON matching the contract, and JUnit XML that a CI runner renders.
- [ ] `test_verdict.py` covers all four verdict tiers and the coverage logic; at least one check per family has a test against a fixture.

### Phase 2 (binary)
- [ ] `plumb check tableau --workbook f.twbx` parses the workbook and reports the Tableau static catalog, including a FIXED LOD inventory and any custom SQL.
- [ ] The local web UI runs from one command, loads a query or workbook, runs a profile, and renders the same verdict and report as the CLI.
- [ ] `--explain` attaches AI explanations to failing checks and demonstrably never alters a status (tested by asserting verdict equality with and without the flag).
- [ ] Shared baselines can be written to and read from the configured store, and a teammate's machine reproduces the same diff result.

---

## Build sequence

```
Phase 0  Scaffold: pyproject, folder tree, pydantic config models and loader,
         Snowflake connect module with auth, query tag, timeout, row cap, and the
         read-only guard. Verdict and coverage logic with full tests first.

Phase 1  SQL engine end to end, in this order:
         1. sql_meta (existence and types) and sql_static (parse-based checks).
         2. sql_assertions (grain, null, RI, domain, range, freshness, dup, recon).
         3. baseline store plus sql_regression (the confidence centerpiece).
         4. sql_performance.
         5. report: HTML, JSON, JUnit. CLI wiring and exit codes.
         6. Security and cost guardrails verified against QUERY_HISTORY. Docs.

Phase 2  Tableau static analysis. Local web UI. Optional AI assist. Shared baselines.

Phase 3  Tableau live reconciliation and Metadata API lineage and consistency.
```

---

## Assumptions to confirm

1. The team works through git and pull requests for at least some SQL, so a central ruleset repo and CI gate are usable. If most analysts are GUI-only, the local CLI and HTML report still deliver the core value and CI becomes optional.
2. Tableau is Server or Cloud with the Metadata API available. This only matters for Phase 3; Phases 1 and 2 do not depend on it.
3. A read-only `PLUMB_QC_ROLE`, a small `PLUMB_WH`, and key-pair or SSO auth can be provisioned. This is the one external dependency for Phase 1.
4. AI assist is wanted but strictly as an assistant. If the org prefers no external LLM calls, the entire `ai/` layer is omitted with zero impact on verdicts.

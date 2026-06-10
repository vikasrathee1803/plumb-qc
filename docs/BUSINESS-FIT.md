# Does Plumb make sense for a real BI team? — validation verdict

_2026-06-10 · three research/validation cycles · live-verified against
PORTFOLIO_DEMO_DB_

## Verdict

**Yes, with a sharpened position.** Plumb is viable as a BI team's
pre-publish QC gate — provided it is positioned as the **last-mile
artifact gate** (the SQL build, the workbook, the migration cut-over) and
not as another pipeline data-quality framework. The three check families
are now one coherent product: same engine, same ruleset, same verdict
tiers, same reports, one CLI.

## Where Plumb sits (cycle-1 research)

| Layer | Owned by | Plumb's relationship |
|---|---|---|
| Pipeline transforms | dbt tests, dbt-expectations | complementary — they test models in the DAG; Plumb tests the ad-hoc SQL builds and artifacts that never enter the DAG |
| Pipeline validation / observability | Great Expectations, Soda | complementary — scheduled monitoring vs Plumb's on-demand pre-publish gate |
| Migration reconciliation | Datafold (cross-db diffing, paid SaaS), DataGaps ETL Validator | overlapping — Plumb's parity family is the local-first, workbook-aware version: it derives WHAT to compare from the Tableau artifact itself, not from a hand-built table list |
| BI artifact testing | DataGaps BI Validator (visual/worksheet regression, paid) | adjacent — Plumb deliberately stays at the data layer (deterministic, read-only); visual regression is explicitly out of scope |

The distinctive combination no surveyed tool offers: **workbook-aware,
local-first, read-only, deterministic-verdict QC that ties the Tableau
artifact to the warehouse objects it depends on** — for free, on the
analyst's machine, with CI-ready exit codes and JUnit.

The honest weaknesses against incumbents: single-warehouse (Snowflake
only), single BI tool (Tableau only), no scheduled monitoring (by
design), no row-level cross-database diffing (deferred, M-HASH-001).

## What live validation found and fixed (cycles 1–2)

Walking every surface as an analyst would, live against a real Snowflake
account, surfaced four adoption blockers — each fixed with a pinned test:

1. `check sql` failed with a baffling parse error on UTF-8-BOM .sql files
   (which PowerShell/SSMS/VS Code write by default on Windows).
2. `plumb doctor` false-FAILed a healthy install (web/ path handling) —
   the worst self-check failure mode, since it teaches users to ignore it.
3. Estate and parity console output buried the verdict under phantom
   "skipped" noise from structurally inapplicable checks.
4. `check tableau` hard-failed (HIGH → REVIEW) every team that had not
   yet configured a certification list, while the SQL counterpart skipped
   — first-run alert fatigue on the exact surface meant to win trust.

Live-verified after fixes: doctor fully green; estate roll-up console is
the table + verdict, nothing else; BOM'd queries pass; all 9 M-* checks
PASS on the identity wave including custom-SQL column metrics via the
SYSTEM$TYPEOF probe and the post-swap inversion path.

## Roadmap (ranked, not started)

0. ~~Row-hash deep compare (M-HASH-001)~~ — **shipped 2026-06-10**
   (built first by user decision; live-verified, see PARITY-PLAN-V2
   item 6 amendment).
1. **Calc-level parity (M-CALC-001)** — deferred in PARITY-PLAN-V2 §2
   until the estate runner measures the uncovered-calc surface on a real
   wave; that instrumentation now exists.
2. **Power BI ingestion** — the engine, verdict, and report layers are
   artifact-agnostic; a .pbix/PBIP source extractor would double the
   addressable BI estate. Large; needs its own plan.
3. **Scheduled re-verification** — a thin `plumb watch` over the existing
   snapshot/check loop would answer the observability gap without
   becoming Soda; consider only if teams ask.
4. **Published-datasource contract check** — the team's workbooks use
   standalone published data sources, so a workbook-side live column
   check sees nothing; the honest version interrogates the PUBLISHED
   datasource via Tableau's REST/Metadata API (tableauserverclient is
   already a dependency, PAT auth already configured in settings) and
   compares its columns to the warehouse objects. Needs its own plan.

Shipped 2026-06-10 (daily-QC uplift wave): `--save-baseline` +
canonical auto-baselines (R-* checks arm themselves after one save, CLI
and web), T-CALC-003 deleted-calculation references (narrowed to
internal calc names after the fixture proved bare-ref judging would be
noise), and the engine now converts a crashing check into an honest
ERROR result instead of a crashed run.

Rejected 2026-06-10 (user decision): estate-style bulk for
`check tableau`/`check sql` — real workbook files are heavy, and parsing
a whole dashboards/ folder per CI run costs more than the roll-up is
worth. Single-artifact checks plus the parity-only estate stay the model.

## What NOT to build

- Visual/pixel regression (BI Validator's lane; conflicts with the
  deterministic data-layer stance).
- A dbt plugin or pipeline scheduler (different layer; integration docs
  suffice).
- Tableau Cloud pulling inside Plumb (ADR D10 stands: Autopilot downloads,
  Plumb consumes local files).

## Sources

- Datafold cross-database diffing and migration validation:
  https://www.datafold.com/data-migration ·
  https://www.datafold.com/blog/evolved-cross-database-diffing ·
  https://github.com/datafold/data-diff
- DataGaps BI Validator (Tableau testing automation):
  https://www.datagaps.com/bi-testing-tools/bi-validator/automate-tableau-testing/
- Data-quality tool landscape:
  https://cybersierra.co/blog/best-data-quality-tools/ ·
  https://www.dataexpert.io/blog/soda-vs-great-expectations-data-quality-tools ·
  https://lakefs.io/data-quality/data-quality-tools/ ·
  https://atlan.com/open-source-data-quality-tools/
- dbt data-quality testing with dbt-expectations:
  https://www.datadoghq.com/blog/dbt-data-quality-testing/
- Tableau performance/QA guidance:
  https://help.tableau.com/current/pro/desktop/en-us/perf_checklist.htm

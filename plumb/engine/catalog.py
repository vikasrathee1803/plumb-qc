"""The check catalog: registry definitions plus UI-facing metadata.

This is what surfaces the configurable check list. It joins the registry
(id, name, family, default severity, execution type) with hand-authored
param hints and one-line descriptions so a UI can render toggles and the
right inputs per check. Adding a check still only means registering it;
adding param hints here is optional and only improves the editor.
"""

from __future__ import annotations

from typing import Any

from plumb.engine.registry import all_checks

# Per-check parameter hints for the configuration UI. type is one of
# "list", "str", "int", "float", "bool", "sql". Checks not listed take no
# per-run params (some read ruleset-level lists like certified_sources).
PARAM_HINTS: dict[str, list[dict[str, Any]]] = {
    "D-GRAIN-001": [{"name": "key", "type": "list", "required": True}],
    "D-GRAIN-002": [
        {"name": "min_rows", "type": "int"},
        {"name": "max_rows", "type": "int"},
    ],
    "D-NULL-001": [{"name": "key", "type": "list", "required": True}],
    "D-NULL-002": [
        {"name": "columns", "type": "list", "required": True},
        {"name": "threshold", "type": "float"},
    ],
    "D-RI-001": [
        {"name": "fk_column", "type": "str", "required": True},
        {"name": "ref_table", "type": "str", "required": True},
        {"name": "ref_column", "type": "str", "required": True},
    ],
    "D-DOMAIN-001": [
        {"name": "column", "type": "str", "required": True},
        {"name": "allowed", "type": "list", "required": True},
    ],
    "D-RANGE-001": [
        {"name": "column", "type": "str", "required": True},
        {"name": "min", "type": "float"},
        {"name": "max", "type": "float"},
    ],
    "D-FRESH-001": [
        {"name": "event_ts_col", "type": "str", "required": True},
        {"name": "sla_hours", "type": "float"},
    ],
    "D-RECON-001": [
        {"name": "metric_sql", "type": "sql", "required": True},
        {"name": "source_of_truth_sql", "type": "sql", "required": True},
        {"name": "tolerance_abs", "type": "float"},
        {"name": "tolerance_pct", "type": "float"},
    ],
    "D-BLANK-001": [
        {"name": "columns", "type": "list", "required": True},
        {"name": "threshold", "type": "float"},
    ],
    "D-POS-001": [{"name": "columns", "type": "list", "required": True}],
    "D-DISTINCT-001": [
        {"name": "column", "type": "str", "required": True},
        {"name": "min", "type": "int"},
        {"name": "max", "type": "int"},
    ],
    "P-COST-001": [
        {"name": "max_partitions", "type": "int"},
        {"name": "max_bytes", "type": "int"},
    ],
    "T-RLS-001": [{"name": "required", "type": "bool"}],
    "T-FILT-001": [{"name": "max_filters", "type": "int"}],
    "M-ROW-001": [{"name": "tolerance_pct", "type": "float"}],
}

# One-line descriptions for the catalog. Falls back to the registered name.
DESCRIPTIONS: dict[str, str] = {
    "S-STAT-001": "Flags SELECT * in production SQL.",
    "S-STAT-002": "Catches cross or cartesian joins with no join condition.",
    "S-STAT-003": "Catches NOT IN (subquery), the NULL trap.",
    "S-STAT-010": "Heuristic: DISTINCT masking a join fan-out.",
    "S-STAT-011": "ORDER BY in a subquery or CTE that the optimizer discards.",
    "S-META-001": "Confirms every referenced table and view exists.",
    "D-GRAIN-001": "Proves the declared key is unique (no fan-out).",
    "D-NULL-001": "Proves key columns are never null.",
    "D-FRESH-001": "Max event timestamp is within the freshness SLA.",
    "D-RECON-001": "Aggregates tie to a source of truth within tolerance.",
    "D-DUP-001": "Detects fully duplicated rows.",
    "D-BLANK-001": "Empty or whitespace string rate within threshold.",
    "D-POS-001": "Declared numeric columns have no negative values.",
    "D-DISTINCT-001": "Distinct value count within expected bounds.",
    "R-DIFF-001": "Row and aggregate diff against a saved baseline.",
    "M-SRC-001": "Workbook datasources decompose to provable Snowflake relations.",
    "M-MAP-001": "Every source resolves to a target object via the map (or identity).",
    "M-SNAP-001": "A legacy snapshot exists for every resolved source.",
    "M-SCHEMA-001": "Target objects carry the snapshot's columns with compatible types.",
    "M-ROW-001": "Row counts match the legacy snapshot within tolerance.",
    "M-AGG-001": "SUM/MIN/MAX match the snapshot per numeric column.",
    "M-NULL-001": "Per-column null counts match, relative to table size.",
    "M-DIST-001": "COUNT DISTINCT matches on the map's declared keys.",
    "M-GRAIN-001": "Grouped row counts match per declared grain.",
    "M-HASH-001": "Per-row fingerprints match on keyed objects (cell-level drift).",
    "M-ESTATE-001": "Estate roll-up: no workbook in the wave is blocked or errored.",
    "M-ESTATE-002": "Estate roll-up: no workbook in the wave needs review.",
}


def catalog() -> list[dict[str, Any]]:
    """Every registered check with UI metadata, sorted by family then id."""
    out: list[dict[str, Any]] = []
    for d in all_checks():
        # Custom assertions are authored in a dedicated UI, not toggled here.
        if d.check_id.startswith("D-CUSTOM"):
            continue
        out.append(
            {
                "id": d.check_id,
                "name": d.name,
                "family": d.family.value,
                "default_severity": d.default_severity.value,
                "execution_type": d.execution_type.value,
                "description": DESCRIPTIONS.get(d.check_id, d.name),
                "params": PARAM_HINTS.get(d.check_id, []),
            }
        )
    out.sort(key=lambda c: (c["family"], c["id"]))
    return out

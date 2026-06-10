"""Migration parity checks (M-* catalog, PARITY-PLAN S4.1).

Pure comparisons over the ParityBundle assembled by parity/runner.py and
carried in CheckContext.extras["parity_bundle"]. The checks run no SQL
themselves: structural checks (M-SRC/M-MAP/M-SNAP) read the bundle's
relations and mapping resolution; value checks compare live metrics against
the legacy snapshots within declared tolerances. A measurement failure on
any relation surfaces as ERROR, never as a pass. Evidence carries
aggregates only — never raw data rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from plumb.checks._base import build_result
from plumb.engine.models import CheckFamily, CheckResult, ExecutionType, Severity, Status
from plumb.engine.registry import CheckContext, register_check
from plumb.parity.contracts import (
    EXTRAS_KEY,
    MappingResolution,
    ParityBundle,
    ParityMetrics,
    ResolvedObject,
    snapshot_name,
)

NO_BUNDLE = "no parity bundle (not a parity run)"
NO_RESOLUTION = "no mapping resolution"
SNAPSHOT_PHASE = "snapshot phase"

_DETAIL_CAP = 10
_EPS = 1e-9

_NUMERIC_TYPES = {
    "NUMBER", "DECIMAL", "NUMERIC", "INT", "INTEGER", "BIGINT", "SMALLINT",
    "TINYINT", "BYTEINT", "FLOAT", "FLOAT4", "FLOAT8", "DOUBLE",
    "DOUBLE PRECISION", "REAL", "FIXED",
}
_TEXT_TYPES = {"TEXT", "VARCHAR", "CHAR", "CHARACTER", "STRING"}
_DATETIME_TYPES = {"DATE", "DATETIME", "TIME", "TIMESTAMP"}
_BOOLEAN_TYPES = {"BOOLEAN"}


def _bundle(ctx: CheckContext) -> ParityBundle | None:
    """The shared guard: every M-* check SKIPs outside a parity run."""
    candidate = ctx.extras.get(EXTRAS_KEY) if ctx.extras else None
    if not isinstance(candidate, ParityBundle):
        return None
    return candidate


def _no_bundle_skip(ctx: CheckContext, check_id: str) -> CheckResult:
    return build_result(ctx, check_id, Status.SKIP, observed=NO_BUNDLE)


def _named(items: list[str]) -> str:
    """Join up to _DETAIL_CAP items; summarize the rest as 'and N more'."""
    if len(items) <= _DETAIL_CAP:
        return ", ".join(items)
    return ", ".join(items[:_DETAIL_CAP]) + f" and {len(items) - _DETAIL_CAP} more"


def _rel_diff(old: float, new: float) -> float:
    """Relative difference; both-zero counts as equal."""
    if old == 0 and new == 0:
        return 0.0
    return abs(new - old) / max(abs(old), _EPS)


def _type_category(data_type: str) -> str:
    """Coarse type category for drift comparison (Snowflake DATA_TYPE names)."""
    base = data_type.upper().split("(", 1)[0].strip()
    if base in _NUMERIC_TYPES:
        return "numeric"
    if base in _TEXT_TYPES:
        return "text"
    if base in _DATETIME_TYPES or base.startswith("TIMESTAMP"):
        return "date-time"
    if base in _BOOLEAN_TYPES:
        return "boolean"
    return "other"


@dataclass(frozen=True)
class _Pair:
    """A resolved relation with both sides measured: ready to compare."""

    resolved: ResolvedObject
    snap: ParityMetrics
    live: ParityMetrics


def _value_phase_skip(ctx: CheckContext, check_id: str, bundle: ParityBundle) -> CheckResult | None:
    """Common SKIP gates for the value-comparison checks."""
    if bundle.mode == "snapshot":
        return build_result(ctx, check_id, Status.SKIP, observed=SNAPSHOT_PHASE)
    if bundle.live_unavailable_reason:
        return build_result(
            ctx, check_id, Status.SKIP, observed=bundle.live_unavailable_reason
        )
    if bundle.resolution is None:
        return build_result(ctx, check_id, Status.SKIP, observed=NO_RESOLUTION)
    return None


def _comparable(
    bundle: ParityBundle, resolution: MappingResolution
) -> tuple[list[_Pair], list[tuple[ResolvedObject, str]]]:
    """Split resolved objects into comparable pairs and measurement errors.

    Relations missing a snapshot belong to M-SNAP-001; relations missing the
    live side without a recorded error belong to M-SCHEMA-001. Both are
    silently excluded here so each gap is reported exactly once.
    """
    pairs: list[_Pair] = []
    errored: list[tuple[ResolvedObject, str]] = []
    for resolved in resolution.resolved:
        name = snapshot_name(bundle.snapshot_prefix, resolved.relation)
        snap = bundle.snapshots.get(name)
        if snap is None:
            continue
        if name in bundle.errors:
            errored.append((resolved, bundle.errors[name]))
            continue
        live = bundle.live_metrics.get(name)
        if live is None:
            continue
        pairs.append(_Pair(resolved=resolved, snap=snap, live=live))
    return pairs, errored


def _measurement_error(
    ctx: CheckContext, check_id: str, errored: list[tuple[ResolvedObject, str]]
) -> CheckResult:
    """A failed measurement must never read as a pass: overall ERROR."""
    detail = _named([f"{r.target_fqn} ({msg})" for r, msg in errored])
    return build_result(
        ctx,
        check_id,
        Status.ERROR,
        observed=f"measurement failed for {len(errored)} relation(s): {detail}",
        remediation="Fix the measurement failures (object access, SQL errors) and re-run.",
    )


@register_check(
    check_id="M-SRC-001",
    name="Workbook sources are parity-eligible",
    family=CheckFamily.MIGRATION_PARITY,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.STATIC,
)
def m_src_001(ctx: CheckContext, params: dict[str, Any]) -> CheckResult:
    bundle = _bundle(ctx)
    if bundle is None:
        return _no_bundle_skip(ctx, "M-SRC-001")
    eligible = [r for r in bundle.relations if r.kind in ("table", "custom_sql")]
    refused = [r for r in bundle.relations if r.kind == "refused"]
    if not eligible:
        return build_result(
            ctx,
            "M-SRC-001",
            Status.FAIL,
            observed="0 parity-eligible relations in the workbook",
            expected="at least one table or custom-SQL relation",
            remediation="Join/union/extract-only datasources are refused (PARITY-PLAN D6); "
            "parity needs at least one table or custom-SQL relation.",
        )
    if refused:
        names = _named([f"{r.datasource} ({r.refusal_reason or 'unknown'})" for r in refused])
        return build_result(
            ctx,
            "M-SRC-001",
            Status.WARN,
            observed=f"{len(refused)} datasource(s) refused: {names}",
            expected="every datasource decomposes to table or custom-SQL relations",
            remediation="Refused datasources are not covered by parity; verify them manually "
            "or restructure to single-table / custom-SQL sources.",
        )
    tables = sum(1 for r in eligible if r.kind == "table")
    custom = sum(1 for r in eligible if r.kind == "custom_sql")
    return build_result(
        ctx,
        "M-SRC-001",
        Status.PASS,
        observed=f"{tables} table relation(s), {custom} custom-SQL",
    )


@register_check(
    check_id="M-MAP-001",
    name="Every source resolves to a target object",
    family=CheckFamily.MIGRATION_PARITY,
    default_severity=Severity.BLOCKER,
    execution_type=ExecutionType.STATIC,
)
def m_map_001(ctx: CheckContext, params: dict[str, Any]) -> CheckResult:
    bundle = _bundle(ctx)
    if bundle is None:
        return _no_bundle_skip(ctx, "M-MAP-001")
    resolution = bundle.resolution
    if resolution is None:
        return build_result(ctx, "M-MAP-001", Status.SKIP, observed=NO_RESOLUTION)
    if resolution.unmapped:
        names = _named([r.fqn or r.label for r in resolution.unmapped])
        return build_result(
            ctx,
            "M-MAP-001",
            Status.FAIL,
            observed=f"{len(resolution.unmapped)} unmapped source(s): {names}",
            expected="every eligible relation maps to a target object (or identity)",
            remediation="Add an explicit old/new entry to the map file for each named "
            "object, or list it under ignore. Plumb never guesses a mapping.",
        )
    explicit = sum(1 for r in resolution.resolved if not r.via_identity)
    identity = sum(1 for r in resolution.resolved if r.via_identity)
    return build_result(
        ctx,
        "M-MAP-001",
        Status.PASS,
        observed=f"{explicit} explicit, {identity} identity, {len(resolution.ignored)} ignored",
    )


@register_check(
    check_id="M-SNAP-001",
    name="A legacy snapshot exists for every source",
    family=CheckFamily.MIGRATION_PARITY,
    default_severity=Severity.BLOCKER,
    execution_type=ExecutionType.STATIC,
)
def m_snap_001(ctx: CheckContext, params: dict[str, Any]) -> CheckResult:
    bundle = _bundle(ctx)
    if bundle is None:
        return _no_bundle_skip(ctx, "M-SNAP-001")
    resolution = bundle.resolution
    if resolution is None:
        return build_result(ctx, "M-SNAP-001", Status.SKIP, observed=NO_RESOLUTION)
    if bundle.mode == "snapshot":
        # Snapshot phase: this check verifies the writes that just happened.
        # A measurement or store failure here must never be invisible — the
        # snapshot run's verdict is the analyst's signal that the legacy
        # side was captured completely (coverage honesty, PARITY-PLAN §4).
        if bundle.live_unavailable_reason is not None:
            return build_result(
                ctx, "M-SNAP-001", Status.SKIP, observed=bundle.live_unavailable_reason
            )
        if bundle.errors:
            return _measurement_error(
                ctx,
                "M-SNAP-001",
                [
                    (r, bundle.errors[snapshot_name(bundle.snapshot_prefix, r.relation)])
                    for r in resolution.resolved
                    if snapshot_name(bundle.snapshot_prefix, r.relation) in bundle.errors
                ],
            )
        unwritten = [
            snapshot_name(bundle.snapshot_prefix, r.relation)
            for r in resolution.resolved
            if snapshot_name(bundle.snapshot_prefix, r.relation) not in bundle.live_metrics
        ]
        if unwritten:
            return build_result(
                ctx,
                "M-SNAP-001",
                Status.FAIL,
                observed=f"{len(unwritten)} snapshot(s) not written: {_named(unwritten)}",
                expected="every resolved relation snapshotted this run",
            )
        return build_result(
            ctx,
            "M-SNAP-001",
            Status.PASS,
            observed=f"{len(resolution.resolved)} snapshot(s) written",
        )
    missing = [
        snapshot_name(bundle.snapshot_prefix, r.relation)
        for r in resolution.resolved
        if snapshot_name(bundle.snapshot_prefix, r.relation) not in bundle.snapshots
    ]
    if missing:
        return build_result(
            ctx,
            "M-SNAP-001",
            Status.FAIL,
            observed=f"{len(missing)} missing snapshot(s): {_named(missing)}",
            expected="a legacy snapshot per resolved relation",
            remediation=(
                f"run: plumb parity snapshot --workbook {bundle.workbook_path} "
                "--profile <legacy-profile> --map <map.yml>"
            ),
        )
    return build_result(
        ctx,
        "M-SNAP-001",
        Status.PASS,
        observed=f"{len(resolution.resolved)} snapshot(s) present",
    )


@register_check(
    check_id="M-SCHEMA-001",
    name="Target object exists with required columns and types",
    family=CheckFamily.MIGRATION_PARITY,
    default_severity=Severity.BLOCKER,
    execution_type=ExecutionType.EXECUTION,
)
def m_schema_001(ctx: CheckContext, params: dict[str, Any]) -> CheckResult:
    bundle = _bundle(ctx)
    if bundle is None:
        return _no_bundle_skip(ctx, "M-SCHEMA-001")
    gate = _value_phase_skip(ctx, "M-SCHEMA-001", bundle)
    if gate is not None:
        return gate
    resolution = bundle.resolution
    assert resolution is not None  # _value_phase_skip guarantees it
    failures: list[str] = []
    errors: list[tuple[ResolvedObject, str]] = []
    evidence: list[dict[str, Any]] = []
    checked = 0
    for resolved in resolution.resolved:
        if resolved.relation.kind == "custom_sql":
            continue  # only row-count metrics exist: schema check is vacuous
        name = snapshot_name(bundle.snapshot_prefix, resolved.relation)
        snap = bundle.snapshots.get(name)
        if snap is None:
            continue  # M-SNAP-001 owns missing snapshots
        live = bundle.live_metrics.get(name)
        if live is None:
            message = bundle.errors.get(name)
            if message is None:
                continue
            if "not found" in message.lower():
                failures.append(f"{resolved.target_fqn}: target object not found")
                evidence.append(
                    {"object": resolved.target_fqn, "column": None, "issue": "object not found"}
                )
            else:
                errors.append((resolved, message))
            continue
        checked += 1
        missing_cols = sorted(set(snap.columns) - set(live.columns))
        if missing_cols:
            failures.append(
                f"{resolved.target_fqn}: missing column(s) {_named(missing_cols)}"
            )
            evidence.extend(
                {"object": resolved.target_fqn, "column": c, "issue": "missing in target"}
                for c in missing_cols
            )
        for col in sorted(set(snap.columns) & set(live.columns)):
            old_cat = _type_category(snap.columns[col].data_type)
            new_cat = _type_category(live.columns[col].data_type)
            if old_cat != new_cat:
                failures.append(f"{resolved.target_fqn}: {col} type {old_cat} -> {new_cat}")
                evidence.append(
                    {
                        "object": resolved.target_fqn,
                        "column": col,
                        "issue": "type category changed",
                        "legacy": snap.columns[col].data_type,
                        "target": live.columns[col].data_type,
                    }
                )
    if errors:
        return _measurement_error(ctx, "M-SCHEMA-001", errors)
    if failures:
        return build_result(
            ctx,
            "M-SCHEMA-001",
            Status.FAIL,
            observed=_named(failures),
            expected="every snapshot column present on the target with a compatible type",
            evidence_rows=evidence[:_DETAIL_CAP],
            remediation="Add the missing columns / fix the types in the target presentation "
            "layer, or map renamed columns in the map file.",
        )
    return build_result(
        ctx,
        "M-SCHEMA-001",
        Status.PASS,
        observed=f"{checked} object(s) schema-compatible with their snapshots",
    )


@register_check(
    check_id="M-ROW-001",
    name="Row count parity",
    family=CheckFamily.MIGRATION_PARITY,
    default_severity=Severity.BLOCKER,
    execution_type=ExecutionType.EXECUTION,
)
def m_row_001(ctx: CheckContext, params: dict[str, Any]) -> CheckResult:
    bundle = _bundle(ctx)
    if bundle is None:
        return _no_bundle_skip(ctx, "M-ROW-001")
    gate = _value_phase_skip(ctx, "M-ROW-001", bundle)
    if gate is not None:
        return gate
    resolution = bundle.resolution
    assert resolution is not None
    pairs, errored = _comparable(bundle, resolution)
    if errored:
        return _measurement_error(ctx, "M-ROW-001", errored)
    # Row drift is the loudest migration signal: the run-level param wins over
    # per-object map tolerances, and it defaults to exact.
    tol = float(params.get("tolerance_pct", 0.0))
    evidence: list[dict[str, Any]] = []
    breaches: list[tuple[float, str, int, int]] = []
    for pair in pairs:
        old, new = pair.snap.row_count, pair.live.row_count
        diff = _rel_diff(old, new)
        evidence.append(
            {
                "object": pair.resolved.target_fqn,
                "legacy_rows": old,
                "target_rows": new,
                "diff_pct": round(diff * 100, 4),
            }
        )
        if diff > tol:
            breaches.append((diff, pair.resolved.target_fqn, old, new))
    if breaches:
        breaches.sort(key=lambda b: (-b[0], b[1]))
        _, fqn, old, new = breaches[0]
        signed = (new - old) / max(abs(old), _EPS) * 100
        return build_result(
            ctx,
            "M-ROW-001",
            Status.FAIL,
            observed=(
                f"{len(breaches)} of {len(pairs)} object(s) breach row-count tolerance "
                f"{tol}; worst: {old} vs {new} ({signed:+.1f}%) on {fqn}"
            ),
            expected=f"row counts equal within tolerance {tol}",
            evidence_rows=evidence[:_DETAIL_CAP],
            remediation="The target object dropped or gained rows vs the legacy snapshot; "
            "check the new layer's joins and filters before publishing.",
        )
    return build_result(
        ctx,
        "M-ROW-001",
        Status.PASS,
        observed=f"row counts match within tolerance {tol} for {len(pairs)} object(s)",
        evidence_rows=evidence[:_DETAIL_CAP],
    )


@register_check(
    check_id="M-AGG-001",
    name="Numeric aggregate parity (SUM/MIN/MAX)",
    family=CheckFamily.MIGRATION_PARITY,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.EXECUTION,
)
def m_agg_001(ctx: CheckContext, params: dict[str, Any]) -> CheckResult:
    bundle = _bundle(ctx)
    if bundle is None:
        return _no_bundle_skip(ctx, "M-AGG-001")
    gate = _value_phase_skip(ctx, "M-AGG-001", bundle)
    if gate is not None:
        return gate
    resolution = bundle.resolution
    assert resolution is not None
    pairs, errored = _comparable(bundle, resolution)
    if errored:
        return _measurement_error(ctx, "M-AGG-001", errored)
    breaches: list[tuple[float, dict[str, Any]]] = []
    compared = 0
    for pair in pairs:
        tol = pair.resolved.tolerance_pct
        for col in sorted(set(pair.snap.columns) & set(pair.live.columns)):
            old_col, new_col = pair.snap.columns[col], pair.live.columns[col]
            for metric, old, new in (
                ("sum", old_col.sum_value, new_col.sum_value),
                ("min", old_col.min_value, new_col.min_value),
                ("max", old_col.max_value, new_col.max_value),
            ):
                if old is None or new is None:
                    continue
                compared += 1
                diff = _rel_diff(old, new)
                if diff > tol:
                    breaches.append(
                        (
                            diff,
                            {
                                "object": pair.resolved.target_fqn,
                                "column": col,
                                "metric": metric,
                                "legacy": old,
                                "target": new,
                                "diff_pct": round(diff * 100, 4),
                            },
                        )
                    )
    if breaches:
        breaches.sort(key=lambda b: (-b[0], b[1]["object"], b[1]["column"], b[1]["metric"]))
        names = _named(
            [f"{b['metric'].upper()}({b['column']}) on {b['object']}" for _, b in breaches]
        )
        return build_result(
            ctx,
            "M-AGG-001",
            Status.FAIL,
            observed=f"{len(breaches)} aggregate breach(es) beyond tolerance: {names}",
            expected="SUM/MIN/MAX equal within each object's tolerance_pct",
            evidence_rows=[b for _, b in breaches[:_DETAIL_CAP]],
            remediation="A summed or bounding value drifted between legacy and target; "
            "investigate the named columns before publishing.",
        )
    return build_result(
        ctx,
        "M-AGG-001",
        Status.PASS,
        observed=f"{compared} aggregate(s) match within tolerance across {len(pairs)} object(s)",
    )


@register_check(
    check_id="M-NULL-001",
    name="Null-count parity",
    family=CheckFamily.MIGRATION_PARITY,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.EXECUTION,
)
def m_null_001(ctx: CheckContext, params: dict[str, Any]) -> CheckResult:
    bundle = _bundle(ctx)
    if bundle is None:
        return _no_bundle_skip(ctx, "M-NULL-001")
    gate = _value_phase_skip(ctx, "M-NULL-001", bundle)
    if gate is not None:
        return gate
    resolution = bundle.resolution
    assert resolution is not None
    pairs, errored = _comparable(bundle, resolution)
    if errored:
        return _measurement_error(ctx, "M-NULL-001", errored)
    breaches: list[tuple[float, dict[str, Any]]] = []
    compared = 0
    for pair in pairs:
        tol = pair.resolved.tolerance_pct
        for col in sorted(set(pair.snap.columns) & set(pair.live.columns)):
            compared += 1
            old = pair.snap.columns[col].null_count
            new = pair.live.columns[col].null_count
            # Null drift is judged relative to the table size, not to the
            # (possibly tiny) legacy null count, so one stray null in a
            # million rows does not breach.
            diff = abs(new - old) / max(pair.snap.row_count, 1)
            if diff > tol:
                breaches.append(
                    (
                        diff,
                        {
                            "object": pair.resolved.target_fqn,
                            "column": col,
                            "legacy_nulls": old,
                            "target_nulls": new,
                            "diff_pct": round(diff * 100, 4),
                        },
                    )
                )
    if breaches:
        breaches.sort(key=lambda b: (-b[0], b[1]["object"], b[1]["column"]))
        names = _named([f"{b['column']} on {b['object']}" for _, b in breaches])
        return build_result(
            ctx,
            "M-NULL-001",
            Status.FAIL,
            observed=f"{len(breaches)} column(s) breach null-count tolerance: {names}",
            expected="per-column null counts equal within tolerance, relative to row count",
            evidence_rows=[b for _, b in breaches[:_DETAIL_CAP]],
            remediation="Null drift usually means a lost join or an unmapped default in the "
            "new layer; inspect the named columns.",
        )
    return build_result(
        ctx,
        "M-NULL-001",
        Status.PASS,
        observed=f"null counts match for {compared} column(s) across {len(pairs)} object(s)",
    )


@register_check(
    check_id="M-DIST-001",
    name="Distinct-count parity on keys",
    family=CheckFamily.MIGRATION_PARITY,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.EXECUTION,
)
def m_dist_001(ctx: CheckContext, params: dict[str, Any]) -> CheckResult:
    bundle = _bundle(ctx)
    if bundle is None:
        return _no_bundle_skip(ctx, "M-DIST-001")
    gate = _value_phase_skip(ctx, "M-DIST-001", bundle)
    if gate is not None:
        return gate
    resolution = bundle.resolution
    assert resolution is not None
    if not any(r.keys for r in resolution.resolved):
        return build_result(
            ctx, "M-DIST-001", Status.SKIP, observed="no keys declared in map"
        )
    pairs, errored = _comparable(bundle, resolution)
    errored = [(r, msg) for r, msg in errored if r.keys]
    if errored:
        return _measurement_error(ctx, "M-DIST-001", errored)
    breaches: list[tuple[float, dict[str, Any]]] = []
    compared = 0
    for pair in (p for p in pairs if p.resolved.keys):
        tol = pair.resolved.tolerance_pct
        for key in pair.resolved.keys:
            if key not in pair.snap.distinct_counts or key not in pair.live.distinct_counts:
                continue
            compared += 1
            old = pair.snap.distinct_counts[key]
            new = pair.live.distinct_counts[key]
            diff = _rel_diff(old, new)
            if diff > tol:
                breaches.append(
                    (
                        diff,
                        {
                            "object": pair.resolved.target_fqn,
                            "key": key,
                            "legacy_distinct": old,
                            "target_distinct": new,
                            "diff_pct": round(diff * 100, 4),
                        },
                    )
                )
    if breaches:
        breaches.sort(key=lambda b: (-b[0], b[1]["object"], b[1]["key"]))
        names = _named([f"{b['key']} on {b['object']}" for _, b in breaches])
        return build_result(
            ctx,
            "M-DIST-001",
            Status.FAIL,
            observed=f"{len(breaches)} key(s) breach distinct-count tolerance: {names}",
            expected="COUNT DISTINCT equal within tolerance on every declared key",
            evidence_rows=[b for _, b in breaches[:_DETAIL_CAP]],
            remediation="Distinct-key drift means dropped or duplicated entities in the "
            "target object; check the new layer's grain.",
        )
    return build_result(
        ctx,
        "M-DIST-001",
        Status.PASS,
        observed=f"distinct counts match for {compared} declared key(s)",
    )


def _grain_counts(metrics: ParityMetrics) -> dict[str, int]:
    return {json.dumps(g.group, sort_keys=True): g.count for g in metrics.grain_groups}


@register_check(
    check_id="M-GRAIN-001",
    name="Grain-group parity",
    family=CheckFamily.MIGRATION_PARITY,
    default_severity=Severity.HIGH,
    execution_type=ExecutionType.EXECUTION,
)
def m_grain_001(ctx: CheckContext, params: dict[str, Any]) -> CheckResult:
    bundle = _bundle(ctx)
    if bundle is None:
        return _no_bundle_skip(ctx, "M-GRAIN-001")
    gate = _value_phase_skip(ctx, "M-GRAIN-001", bundle)
    if gate is not None:
        return gate
    resolution = bundle.resolution
    assert resolution is not None
    if not any(r.grain for r in resolution.resolved):
        return build_result(
            ctx, "M-GRAIN-001", Status.SKIP, observed="no grain declared in map"
        )
    pairs, errored = _comparable(bundle, resolution)
    errored = [(r, msg) for r, msg in errored if r.grain]
    if errored:
        return _measurement_error(ctx, "M-GRAIN-001", errored)
    breaches: list[dict[str, Any]] = []
    compared = 0
    for pair in (p for p in pairs if p.resolved.grain):
        compared += 1
        tol = pair.resolved.tolerance_pct
        old_groups = _grain_counts(pair.snap)
        new_groups = _grain_counts(pair.live)
        for group in sorted(old_groups):
            if group not in new_groups:
                breaches.append(
                    {
                        "object": pair.resolved.target_fqn,
                        "group": group,
                        "legacy_count": old_groups[group],
                        "target_count": None,
                        "issue": "missing in target",
                    }
                )
            elif _rel_diff(old_groups[group], new_groups[group]) > tol:
                breaches.append(
                    {
                        "object": pair.resolved.target_fqn,
                        "group": group,
                        "legacy_count": old_groups[group],
                        "target_count": new_groups[group],
                        "issue": "count mismatch",
                    }
                )
        for group in sorted(set(new_groups) - set(old_groups)):
            breaches.append(
                {
                    "object": pair.resolved.target_fqn,
                    "group": group,
                    "legacy_count": None,
                    "target_count": new_groups[group],
                    "issue": "extra in target",
                }
            )
    # Snapshots hold the top-N grain groups (capped at measurement time), so
    # the comparison is over that sample, not every group: say so honestly.
    caveat = "top-N comparison"
    if breaches:
        names = _named([f"{b['group']} on {b['object']} ({b['issue']})" for b in breaches])
        return build_result(
            ctx,
            "M-GRAIN-001",
            Status.FAIL,
            observed=f"{len(breaches)} grain group issue(s) ({caveat}): {names}",
            expected="grouped row counts match per declared grain within tolerance",
            evidence_rows=breaches[:_DETAIL_CAP],
            remediation="A grain group moved between legacy and target; verify the group-by "
            "keys and any re-bucketing in the new layer.",
        )
    return build_result(
        ctx,
        "M-GRAIN-001",
        Status.PASS,
        observed=f"grain groups match for {compared} object(s) ({caveat})",
    )

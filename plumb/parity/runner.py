"""Parity run orchestration: workbook in, RunResult out (PARITY-PLAN §2).

One entry point, run_parity, drives both phases. Snapshot mode measures the
legacy side and persists one Baseline per resolved relation through the
existing baseline store; check mode loads those snapshots, measures the
target side, and lets the M-* checks compare. The engine, verdict, and
report writers are consumed unchanged: this module only assembles the
ParityBundle and hands it to run_checks via CheckContext.extras.

Measurement and store failures never raise out of the measurement loop:
they are recorded per relation in bundle.errors, so the run's verdict
reports them (M-SNAP-001 in snapshot mode, the value checks in check mode)
instead of half-finishing silently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plumb.baseline.store import Baseline, BaselineStore
from plumb.config.models import Ruleset
from plumb.engine.models import RunResult, Target
from plumb.engine.runner import RunRequest, run_checks
from plumb.parity.contracts import (
    EXTRAS_KEY,
    RECORD_COLUMNS,
    ParityBundle,
    ParityMetrics,
    ParityMode,
    snapshot_name,
    snapshot_prefix_for,
)
from plumb.parity.mapping import ParityMap, load_map, resolve, resolve_post_swap
from plumb.parity.metrics import ParityMetricsError, measure
from plumb.parity.sources import extract_relations

NO_SESSION_REASON = "no Snowflake session (static-only run)"


def build_bundle(
    workbook: Path,
    map_path: Path | None,
    mode: ParityMode,
    *,
    post_swap: bool = False,
    snapshot_prefix: str | None = None,
) -> ParityBundle:
    """Extract relations, resolve the map, and assemble the bundle skeleton.

    Raises TableauParseError for unreadable workbooks and ConfigError for a
    bad map file — both before any session is touched, so a broken input
    never costs a warehouse query.

    post_swap (PARITY-PLAN-V2 D14/D18): the workbook is the already-swapped
    artifact carrying NEW object names; the map is applied inverted
    (new->old) to recover each relation's legacy snapshot identity. Only
    the check phase may run post-swap — a snapshot is by definition taken
    from the pre-swap legacy side.

    snapshot_prefix overrides the filename-derived prefix so an estate
    manifest can disambiguate two same-stem workbooks (D13); both phases
    must pass the same override or the check will not find its snapshots.
    """
    if post_swap and mode != "check":
        raise ValueError("--post-swap applies to the check phase only")
    relations = extract_relations(workbook)
    parity_map = load_map(map_path) if map_path is not None else ParityMap(version=1)
    if post_swap:
        resolution = resolve_post_swap(relations, parity_map)
    else:
        resolution = resolve(relations, parity_map)
    # Target-name index for the --post-swap remediation hint (D18: the hint
    # may suggest the flag, only the analyst may set it).
    new_fqns: set[str] = set()
    for entry in parity_map.objects:
        upper = entry.new.strip().upper()
        new_fqns.add(upper)
        new_fqns.add(".".join(upper.split(".")[-2:]))
    return ParityBundle(
        mode=mode,
        workbook_path=str(workbook),
        relations=relations,
        resolution=resolution,
        snapshot_prefix=snapshot_prefix or snapshot_prefix_for(str(workbook)),
        side="legacy" if mode == "snapshot" else "target",
        post_swap=post_swap,
        map_new_fqns=frozenset(new_fqns),
    )


def run_parity(
    *,
    workbook: Path,
    mode: ParityMode,
    ruleset: Ruleset,
    store: BaselineStore,
    map_path: Path | None = None,
    session: Any | None = None,
    profile_name: str | None = None,
    run_id: str | None = None,
    grain_top_n: int = 20,
    post_swap: bool = False,
    snapshot_prefix: str | None = None,
) -> RunResult:
    """Run one parity phase end to end and return the engine's RunResult."""
    bundle = build_bundle(
        workbook, map_path, mode, post_swap=post_swap, snapshot_prefix=snapshot_prefix
    )

    if mode == "check":
        _load_snapshots(bundle, store)

    if session is None:
        bundle.live_unavailable_reason = NO_SESSION_REASON
    elif mode == "snapshot":
        _measure_and_store(bundle, session, store, ruleset, grain_top_n)
    else:
        _measure_target(bundle, session, grain_top_n)

    return run_checks(
        RunRequest(
            target=Target(type="parity", name=workbook.name, source_ref=str(workbook)),
            ruleset=ruleset,
            profile=profile_name,
            session=session,
            run_id=run_id,
            extras={EXTRAS_KEY: bundle},
        )
    )


def _load_snapshots(bundle: ParityBundle, store: BaselineStore) -> None:
    if bundle.resolution is None:
        return
    for resolved in bundle.resolution.resolved:
        name = snapshot_name(bundle.snapshot_prefix, resolved.relation)
        try:
            baseline = store.load(name)
            if baseline is not None:
                bundle.snapshots[name] = ParityMetrics.from_records(baseline.rows)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # A corrupt or foreign-codec snapshot must fail loudly, never
            # half-load: the relation stays out of bundle.snapshots so
            # M-SNAP-001 reports it, with the cause recorded here.
            bundle.errors[name] = f"snapshot unreadable: {exc}"


def _measure_and_store(
    bundle: ParityBundle,
    session: Any,
    store: BaselineStore,
    ruleset: Ruleset,
    grain_top_n: int,
) -> None:
    """Snapshot phase: measure the legacy side, persist one Baseline per
    relation. A re-snapshot overwrites the previous one (the store's save
    is a whole-file replace)."""
    if bundle.resolution is None:
        return
    for resolved in bundle.resolution.resolved:
        name = snapshot_name(bundle.snapshot_prefix, resolved.relation)
        try:
            metrics = measure(session, resolved, "legacy", grain_top_n=grain_top_n)
        except ParityMetricsError as exc:
            bundle.errors[name] = str(exc)
            continue
        records = metrics.to_records()
        try:
            store.save(
                Baseline(
                    name=name,
                    columns=list(RECORD_COLUMNS),
                    rows=records,
                    row_count=len(records),
                    aggregates={},
                    created_at=datetime.now(timezone.utc).isoformat(),
                    source_ref=f"{bundle.side}:{metrics.object_fqn}",
                    ruleset_version=ruleset.version,
                )
            )
        except (OSError, ValueError, TypeError) as exc:
            # pyarrow raises ArrowInvalid / ArrowTypeError, which subclass
            # ValueError / TypeError; a dropped write must be verdict-visible.
            bundle.errors[name] = f"snapshot write failed: {exc}"
            continue
        bundle.live_metrics[name] = metrics


def _measure_target(bundle: ParityBundle, session: Any, grain_top_n: int) -> None:
    """Check phase: measure the target side, only for relations that have a
    snapshot to compare against (a missing snapshot is M-SNAP-001's finding;
    measuring its target side would spend queries with nothing to prove)."""
    if bundle.resolution is None:
        return
    for resolved in bundle.resolution.resolved:
        name = snapshot_name(bundle.snapshot_prefix, resolved.relation)
        if name not in bundle.snapshots:
            continue
        try:
            bundle.live_metrics[name] = measure(
                session, resolved, "target", grain_top_n=grain_top_n
            )
        except ParityMetricsError as exc:
            bundle.errors[name] = str(exc)

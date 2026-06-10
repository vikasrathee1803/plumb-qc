"""Migration parity check tests (M-* catalog, PARITY-PLAN S4.1).

Each check is exercised through a hand-built ParityBundle in
CheckContext.extras: pass, fail, and skip paths, the tolerance edges, and
the ERROR-never-PASS invariant for measurement failures.
"""

from __future__ import annotations

import pytest

from plumb.checks.parity import (
    m_agg_001,
    m_dist_001,
    m_grain_001,
    m_hash_001,
    m_map_001,
    m_null_001,
    m_row_001,
    m_schema_001,
    m_snap_001,
    m_src_001,
)
from plumb.config.models import Ruleset
from plumb.engine import registry
from plumb.engine.models import Status, Target
from plumb.engine.registry import CheckContext
from plumb.parity.contracts import (
    EXTRAS_KEY,
    ColumnMetrics,
    GrainGroup,
    MappingResolution,
    ParityBundle,
    ParityMetrics,
    ResolvedObject,
    SourceRelation,
    snapshot_name,
)

ALL_CHECK_FNS = [
    m_src_001,
    m_map_001,
    m_snap_001,
    m_schema_001,
    m_row_001,
    m_agg_001,
    m_null_001,
    m_dist_001,
    m_grain_001,
    m_hash_001,
]
VALUE_CHECK_FNS = [
    m_schema_001, m_row_001, m_agg_001, m_null_001, m_dist_001, m_grain_001, m_hash_001,
]
ALL_CHECK_IDS = [
    "M-SRC-001", "M-MAP-001", "M-SNAP-001", "M-SCHEMA-001", "M-ROW-001",
    "M-AGG-001", "M-NULL-001", "M-DIST-001", "M-GRAIN-001", "M-HASH-001",
]

PREFIX = "parity__wb"

REL_ORDERS = SourceRelation(
    datasource="Orders", kind="table", database="LEGACY_DB", schema="SALES", table="ORDERS"
)
REL_ITEMS = SourceRelation(
    datasource="Items", kind="table", database="LEGACY_DB", schema="SALES", table="ITEMS"
)
REL_REFUSED = SourceRelation(datasource="Blended", kind="refused", refusal_reason="join")
REL_CUSTOM = SourceRelation(datasource="Adhoc", kind="custom_sql", custom_sql="SELECT 1 AS X")

ORDERS_TARGET = "GALAXY.PRES.FCT_ORDERS"
ITEMS_TARGET = "GALAXY.PRES.FCT_ITEMS"

ORDERS_NAME = snapshot_name(PREFIX, REL_ORDERS)
ITEMS_NAME = snapshot_name(PREFIX, REL_ITEMS)
CUSTOM_NAME = snapshot_name(PREFIX, REL_CUSTOM)


def one(outcome):
    """Unwrap a check outcome to its main result (m_src_001 returns a list:
    its M-SRC-001 result first, then any M-SRC-002 coverage records)."""
    return outcome[0] if isinstance(outcome, list) else outcome


def ctx_for(bundle: ParityBundle | None = None, extras: dict | None = None) -> CheckContext:
    if extras is None:
        extras = {EXTRAS_KEY: bundle} if bundle is not None else {}
    return CheckContext(
        run_id="t",
        target=Target(type="parity", name="wb"),
        ruleset=Ruleset(version="1"),
        extras=extras,
    )


def metrics(
    fqn: str,
    rows: int = 100,
    columns: dict[str, ColumnMetrics] | None = None,
    distinct: dict[str, int] | None = None,
    grain: list[GrainGroup] | None = None,
) -> ParityMetrics:
    return ParityMetrics(
        object_fqn=fqn,
        row_count=rows,
        columns=columns or {},
        distinct_counts=distinct or {},
        grain_groups=grain or [],
    )


def col(
    dt: str = "NUMBER",
    nulls: int = 0,
    s: float | None = None,
    mn: float | None = None,
    mx: float | None = None,
) -> ColumnMetrics:
    return ColumnMetrics(data_type=dt, null_count=nulls, sum_value=s, min_value=mn, max_value=mx)


def make_bundle(
    mode: str = "check",
    relations: list[SourceRelation] | None = None,
    resolution: MappingResolution | None = None,
    snapshots: dict[str, ParityMetrics] | None = None,
    live: dict[str, ParityMetrics] | None = None,
    errors: dict[str, str] | None = None,
    live_unavailable: str | None = None,
) -> ParityBundle:
    return ParityBundle(
        mode=mode,  # type: ignore[arg-type]
        workbook_path="sales.twbx",
        relations=relations if relations is not None else [REL_ORDERS, REL_ITEMS],
        resolution=resolution,
        snapshot_prefix=PREFIX,
        live_metrics=live or {},
        snapshots=snapshots or {},
        errors=errors or {},
        live_unavailable_reason=live_unavailable,
    )


def two_object_bundle(
    snap_orders: ParityMetrics,
    live_orders: ParityMetrics | None,
    snap_items: ParityMetrics | None = None,
    live_items: ParityMetrics | None = None,
    orders_kw: dict | None = None,
    items_kw: dict | None = None,
    errors: dict[str, str] | None = None,
) -> ParityBundle:
    """A check-phase bundle with the two standard table relations resolved."""
    resolution = MappingResolution(
        resolved=[
            ResolvedObject(relation=REL_ORDERS, target_fqn=ORDERS_TARGET, **(orders_kw or {})),
            ResolvedObject(relation=REL_ITEMS, target_fqn=ITEMS_TARGET, **(items_kw or {})),
        ]
    )
    snapshots = {ORDERS_NAME: snap_orders}
    live = {}
    if live_orders is not None:
        live[ORDERS_NAME] = live_orders
    if snap_items is not None:
        snapshots[ITEMS_NAME] = snap_items
    if live_items is not None:
        live[ITEMS_NAME] = live_items
    return make_bundle(resolution=resolution, snapshots=snapshots, live=live, errors=errors)


def _custom_sql_only_bundle(errors: dict[str, str] | None = None) -> ParityBundle:
    """A check-phase bundle whose only relation is custom SQL (row counts
    only, target_fqn empty)."""
    resolution = MappingResolution(
        resolved=[ResolvedObject(relation=REL_CUSTOM, target_fqn="")]
    )
    live = {} if errors else {CUSTOM_NAME: metrics("custom-sql", rows=10)}
    return make_bundle(
        relations=[REL_CUSTOM],
        resolution=resolution,
        snapshots={CUSTOM_NAME: metrics("custom-sql", rows=10)},
        live=live,
        errors=errors,
    )


class TestRegistration:
    def test_all_nine_checks_registered(self):
        import plumb.checks  # noqa: F401 - registration side effects

        for check_id in ALL_CHECK_IDS:
            assert registry.is_registered(check_id), check_id

    def test_catalog_classification(self):
        import plumb.checks  # noqa: F401
        from plumb.engine.models import CheckFamily, ExecutionType, Severity

        for check_id in ALL_CHECK_IDS:
            assert registry.get_check(check_id).family is CheckFamily.MIGRATION_PARITY
        static = {"M-SRC-001", "M-MAP-001", "M-SNAP-001"}
        for check_id in ALL_CHECK_IDS:
            expected = ExecutionType.STATIC if check_id in static else ExecutionType.EXECUTION
            assert registry.get_check(check_id).execution_type is expected, check_id
        blockers = {"M-MAP-001", "M-SNAP-001", "M-SCHEMA-001", "M-ROW-001"}
        for check_id in ALL_CHECK_IDS:
            expected_sev = Severity.BLOCKER if check_id in blockers else Severity.HIGH
            assert registry.get_check(check_id).default_severity is expected_sev, check_id


class TestNoBundleGuard:
    @pytest.mark.parametrize("fn", ALL_CHECK_FNS)
    def test_no_extras_skips(self, fn):
        res = one(fn(ctx_for(extras={}), {}))
        assert res.status is Status.SKIP
        assert "no parity bundle" in res.observed

    @pytest.mark.parametrize("fn", ALL_CHECK_FNS)
    def test_wrong_type_in_extras_skips(self, fn):
        res = one(fn(ctx_for(extras={EXTRAS_KEY: "not a bundle"}), {}))
        assert res.status is Status.SKIP
        assert "no parity bundle" in res.observed


class TestValueCheckPhaseGates:
    @pytest.mark.parametrize("fn", VALUE_CHECK_FNS)
    def test_snapshot_phase_skips(self, fn):
        res = fn(ctx_for(make_bundle(mode="snapshot")), {})
        assert res.status is Status.SKIP
        assert "snapshot phase" in res.observed

    @pytest.mark.parametrize("fn", VALUE_CHECK_FNS)
    def test_live_unavailable_skips_with_reason(self, fn):
        bundle = make_bundle(
            resolution=MappingResolution(), live_unavailable="static-only run: no profile"
        )
        res = fn(ctx_for(bundle), {})
        assert res.status is Status.SKIP
        assert res.observed == "static-only run: no profile"

    @pytest.mark.parametrize("fn", VALUE_CHECK_FNS)
    def test_no_resolution_skips(self, fn):
        res = fn(ctx_for(make_bundle(resolution=None)), {})
        assert res.status is Status.SKIP
        assert res.observed == "no mapping resolution"


class TestMSrc001:
    def test_all_eligible_passes_with_counts(self):
        bundle = make_bundle(relations=[REL_ORDERS, REL_ITEMS, REL_CUSTOM])
        results = m_src_001(ctx_for(bundle), {})
        assert [r.id for r in results] == ["M-SRC-001"]
        res = results[0]
        assert res.status is Status.PASS
        assert res.observed == "2 table relation(s), 1 custom-SQL"

    def test_refused_warns_naming_datasource_and_reason(self):
        bundle = make_bundle(relations=[REL_ORDERS, REL_REFUSED])
        res = one(m_src_001(ctx_for(bundle), {}))
        assert res.status is Status.WARN
        assert "Blended" in res.observed
        assert "join" in res.observed

    def test_zero_eligible_fails(self):
        res = one(m_src_001(ctx_for(make_bundle(relations=[REL_REFUSED])), {}))
        assert res.status is Status.FAIL

    def test_runs_in_snapshot_mode_too(self):
        bundle = make_bundle(mode="snapshot", relations=[REL_ORDERS])
        res = one(m_src_001(ctx_for(bundle), {}))
        assert res.status is Status.PASS

    def test_each_refusal_emits_an_m_src_002_skip(self):
        """QC F6: every refused relation becomes an M-SRC-002 SKIP record
        so it reaches Coverage.checks_skipped with its reason."""
        from plumb.engine.models import CheckFamily, Severity

        other_refused = SourceRelation(
            datasource="Offline", kind="refused", refusal_reason="extract-only"
        )
        bundle = make_bundle(relations=[REL_ORDERS, REL_REFUSED, other_refused])
        results = m_src_001(ctx_for(bundle), {})
        assert results[0].id == "M-SRC-001"
        assert results[0].status is Status.WARN
        skips = [r for r in results if r.id == "M-SRC-002"]
        assert len(skips) == 2
        for skip in skips:
            assert skip.status is Status.SKIP
            assert skip.severity is Severity.INFO
            assert skip.family is CheckFamily.MIGRATION_PARITY
            assert skip.name == "Refused source relation"
        assert skips[0].observed == "Blended: join"
        assert skips[1].observed == "Offline: extract-only"

    def test_refusal_records_emitted_even_when_main_result_fails(self):
        results = m_src_001(ctx_for(make_bundle(relations=[REL_REFUSED])), {})
        assert results[0].status is Status.FAIL
        assert [r.observed for r in results if r.id == "M-SRC-002"] == ["Blended: join"]


class TestMMap001:
    def test_no_resolution_skips(self):
        res = m_map_001(ctx_for(make_bundle(resolution=None)), {})
        assert res.status is Status.SKIP
        assert res.observed == "no mapping resolution"

    def test_unmapped_fails_naming_fqn(self):
        resolution = MappingResolution(unmapped=[REL_ORDERS, REL_ITEMS])
        res = m_map_001(ctx_for(make_bundle(resolution=resolution)), {})
        assert res.status is Status.FAIL
        assert "LEGACY_DB.SALES.ORDERS" in res.observed
        assert "LEGACY_DB.SALES.ITEMS" in res.observed
        assert "map" in res.remediation

    def test_pass_counts_explicit_identity_ignored(self):
        resolution = MappingResolution(
            resolved=[
                ResolvedObject(relation=REL_ORDERS, target_fqn=ORDERS_TARGET),
                ResolvedObject(
                    relation=REL_ITEMS, target_fqn=REL_ITEMS.fqn or "", via_identity=True
                ),
            ],
            ignored=[REL_CUSTOM],
        )
        res = m_map_001(ctx_for(make_bundle(resolution=resolution)), {})
        assert res.status is Status.PASS
        assert res.observed == "1 explicit, 1 identity, 1 ignored"


class TestMSnap001:
    def _resolution(self) -> MappingResolution:
        return MappingResolution(
            resolved=[
                ResolvedObject(relation=REL_ORDERS, target_fqn=ORDERS_TARGET),
                ResolvedObject(relation=REL_ITEMS, target_fqn=ITEMS_TARGET),
            ]
        )

    # Snapshot phase: M-SNAP-001 verifies the writes that just happened
    # (lead integration change — a failed snapshot must be verdict-visible).

    def test_snapshot_phase_complete_writes_pass(self):
        bundle = make_bundle(
            mode="snapshot",
            resolution=self._resolution(),
            live={
                ORDERS_NAME: metrics(ORDERS_TARGET),
                ITEMS_NAME: metrics(ITEMS_TARGET),
            },
        )
        res = m_snap_001(ctx_for(bundle), {})
        assert res.status is Status.PASS
        assert "2 snapshot(s) written" in (res.observed or "")

    def test_snapshot_phase_unwritten_fails(self):
        bundle = make_bundle(
            mode="snapshot",
            resolution=self._resolution(),
            live={ORDERS_NAME: metrics(ORDERS_TARGET)},
        )
        res = m_snap_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "not written" in (res.observed or "")

    def test_snapshot_phase_measurement_error_is_error(self):
        bundle = make_bundle(
            mode="snapshot",
            resolution=self._resolution(),
            live={ORDERS_NAME: metrics(ORDERS_TARGET)},
            errors={ITEMS_NAME: "object not found: X"},
        )
        res = m_snap_001(ctx_for(bundle), {})
        assert res.status is Status.ERROR

    def test_snapshot_phase_static_only_skips(self):
        bundle = make_bundle(mode="snapshot", resolution=self._resolution())
        bundle.live_unavailable_reason = "no Snowflake session (static-only run)"
        res = m_snap_001(ctx_for(bundle), {})
        assert res.status is Status.SKIP

    def test_no_resolution_skips(self):
        res = m_snap_001(ctx_for(make_bundle(resolution=None)), {})
        assert res.status is Status.SKIP

    def test_missing_snapshot_fails_with_command(self):
        bundle = make_bundle(
            resolution=self._resolution(),
            snapshots={ORDERS_NAME: metrics(ORDERS_TARGET)},
        )
        res = m_snap_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert ITEMS_NAME in res.observed
        assert ORDERS_NAME not in res.observed
        assert "plumb parity snapshot --workbook sales.twbx" in res.remediation

    def test_all_present_passes(self):
        bundle = make_bundle(
            resolution=self._resolution(),
            snapshots={
                ORDERS_NAME: metrics(ORDERS_TARGET),
                ITEMS_NAME: metrics(ITEMS_TARGET),
            },
        )
        res = m_snap_001(ctx_for(bundle), {})
        assert res.status is Status.PASS
        assert "2 snapshot(s)" in res.observed


class TestMSchema001:
    def test_missing_column_fails_naming_it(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col(), "QTY": col()}),
            live_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col()}),
        )
        res = m_schema_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "QTY" in res.observed
        assert any(r.get("column") == "QTY" for r in res.evidence.sample_rows)

    def test_type_category_drift_fails(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col("NUMBER")}),
            live_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col("VARCHAR")}),
        )
        res = m_schema_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "numeric" in res.observed and "text" in res.observed

    def test_same_category_different_type_passes(self):
        bundle = two_object_bundle(
            snap_orders=metrics(
                ORDERS_TARGET, columns={"AMOUNT": col("NUMBER"), "TS": col("TIMESTAMP_NTZ")}
            ),
            live_orders=metrics(
                ORDERS_TARGET, columns={"AMOUNT": col("FLOAT"), "TS": col("TIMESTAMP_LTZ")}
            ),
        )
        res = m_schema_001(ctx_for(bundle), {})
        assert res.status is Status.PASS

    def test_not_found_error_fails_naming_object(self):
        # The exact message prefix metrics._discover_columns emits.
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col()}),
            live_orders=None,
            errors={ORDERS_NAME: f"object not found: {ORDERS_TARGET}"},
        )
        res = m_schema_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert ORDERS_TARGET in res.observed

    def test_incidental_not_found_text_is_error_not_fail(self):
        """QC F9: only the metrics-emitted "object not found:" prefix may
        classify as a missing target; other errors mentioning "not found"
        are measurement errors."""
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col()}),
            live_orders=None,
            errors={ORDERS_NAME: "could not resolve host: not found"},
        )
        res = m_schema_001(ctx_for(bundle), {})
        assert res.status is Status.ERROR
        assert "could not resolve host" in res.observed

    def test_other_measurement_error_errors(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col()}),
            live_orders=None,
            errors={ORDERS_NAME: "statement timeout after 120s"},
        )
        res = m_schema_001(ctx_for(bundle), {})
        assert res.status is Status.ERROR
        assert "timeout" in res.observed

    def test_custom_sql_relation_is_vacuously_skipped(self):
        resolution = MappingResolution(
            resolved=[ResolvedObject(relation=REL_CUSTOM, target_fqn="custom-sql")]
        )
        bundle = make_bundle(
            resolution=resolution,
            snapshots={CUSTOM_NAME: metrics("custom-sql", rows=10)},
            live={CUSTOM_NAME: metrics("custom-sql", rows=10)},
        )
        res = m_schema_001(ctx_for(bundle), {})
        assert res.status is Status.PASS
        assert "0 object(s)" in res.observed


class TestMRow001:
    def test_equal_counts_pass(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, rows=100),
            live_orders=metrics(ORDERS_TARGET, rows=100),
        )
        res = m_row_001(ctx_for(bundle), {})
        assert res.status is Status.PASS

    def test_zero_rows_both_sides_pass(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, rows=0),
            live_orders=metrics(ORDERS_TARGET, rows=0),
        )
        res = m_row_001(ctx_for(bundle), {})
        assert res.status is Status.PASS

    def test_exactly_at_tolerance_passes(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, rows=100),
            live_orders=metrics(ORDERS_TARGET, rows=101),
        )
        res = m_row_001(ctx_for(bundle), {"tolerance_pct": 0.01})
        assert res.status is Status.PASS

    def test_just_over_tolerance_fails(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, rows=100),
            live_orders=metrics(ORDERS_TARGET, rows=102),
        )
        res = m_row_001(ctx_for(bundle), {"tolerance_pct": 0.01})
        assert res.status is Status.FAIL

    def test_fail_names_worst_offender_and_evidence_shape(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, rows=100),
            live_orders=metrics(ORDERS_TARGET, rows=110),
            snap_items=metrics(ITEMS_TARGET, rows=100),
            live_items=metrics(ITEMS_TARGET, rows=101),
        )
        res = m_row_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert f"100 vs 110 (+10.0%) on {ORDERS_TARGET}" in res.observed
        assert len(res.evidence.sample_rows) == 2
        for row in res.evidence.sample_rows:
            assert set(row) == {"object", "legacy_rows", "target_rows", "diff_pct"}

    def test_param_default_overrides_per_object_tolerance(self):
        # The map declares a loose per-object tolerance, but row drift uses
        # the run-level param (default 0.0): drift must still fail.
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, rows=100),
            live_orders=metrics(ORDERS_TARGET, rows=150),
            orders_kw={"tolerance_pct": 1.0},
        )
        res = m_row_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL

    def test_errored_relation_forces_error_even_when_others_pass(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, rows=100),
            live_orders=metrics(ORDERS_TARGET, rows=100),
            snap_items=metrics(ITEMS_TARGET, rows=100),
            errors={ITEMS_NAME: "SQL compilation error"},
        )
        res = m_row_001(ctx_for(bundle), {})
        assert res.status is Status.ERROR
        assert ITEMS_TARGET in res.observed
        assert "SQL compilation error" in res.observed


class TestMAgg001:
    def test_within_tolerance_passes(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col(s=100.0, mn=0.0, mx=9.0)}),
            live_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col(s=100.5, mn=0.0, mx=9.0)}),
            orders_kw={"tolerance_pct": 0.01},
        )
        res = m_agg_001(ctx_for(bundle), {})
        assert res.status is Status.PASS

    def test_breaches_fail_with_worst_offender_first(self):
        bundle = two_object_bundle(
            snap_orders=metrics(
                ORDERS_TARGET, columns={"AMOUNT": col(s=100.0), "QTY": col(s=100.0)}
            ),
            live_orders=metrics(
                ORDERS_TARGET, columns={"AMOUNT": col(s=110.0), "QTY": col(s=200.0)}
            ),
            orders_kw={"tolerance_pct": 0.0},
        )
        res = m_agg_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        # QTY doubled (100%) and must outrank AMOUNT (+10%).
        assert res.evidence.sample_rows[0]["column"] == "QTY"
        assert res.evidence.sample_rows[1]["column"] == "AMOUNT"
        assert set(res.evidence.sample_rows[0]) == {
            "object", "column", "metric", "legacy", "target", "diff_pct",
        }

    def test_per_object_tolerance_override_honored(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col(s=100.0)}),
            live_orders=metrics(ORDERS_TARGET, columns={"AMOUNT": col(s=120.0)}),
            snap_items=metrics(ITEMS_TARGET, columns={"QTY": col(s=100.0)}),
            live_items=metrics(ITEMS_TARGET, columns={"QTY": col(s=101.0)}),
            orders_kw={"tolerance_pct": 0.5},
            items_kw={"tolerance_pct": 0.0},
        )
        res = m_agg_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        breached = {r["object"] for r in res.evidence.sample_rows}
        assert breached == {ITEMS_TARGET}

    def test_metric_missing_on_one_side_not_compared(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, columns={"NOTE": col("TEXT")}),
            live_orders=metrics(ORDERS_TARGET, columns={"NOTE": col("TEXT", s=42.0)}),
            orders_kw={"tolerance_pct": 0.0},
        )
        res = m_agg_001(ctx_for(bundle), {})
        assert res.status is Status.PASS

    def test_custom_sql_only_pass_does_not_overstate_coverage(self):
        """QC F13: custom-SQL pairs carry row counts only; the PASS text
        must not claim aggregate coverage over them."""
        bundle = _custom_sql_only_bundle()
        res = m_agg_001(ctx_for(bundle), {})
        assert res.status is Status.PASS
        assert "0 object(s) with column metrics" in res.observed

    def test_error_naming_uses_label_when_target_fqn_empty(self):
        """QC F13: a custom-SQL relation (target_fqn == "") is named by its
        label in error text, never by an empty string."""
        bundle = _custom_sql_only_bundle(errors={CUSTOM_NAME: "statement timeout"})
        res = m_agg_001(ctx_for(bundle), {})
        assert res.status is Status.ERROR
        assert REL_CUSTOM.label in res.observed
        assert "statement timeout" in res.observed


class TestMNull001:
    def test_null_drift_relative_to_row_count(self):
        # Delta of 10 nulls over 200 rows = 5% of the table.
        snap = metrics(ORDERS_TARGET, rows=200, columns={"AMOUNT": col(nulls=10)})
        live = metrics(ORDERS_TARGET, rows=200, columns={"AMOUNT": col(nulls=20)})
        tight = two_object_bundle(snap, live, orders_kw={"tolerance_pct": 0.04})
        loose = two_object_bundle(snap, live, orders_kw={"tolerance_pct": 0.05})
        assert m_null_001(ctx_for(tight), {}).status is Status.FAIL
        assert m_null_001(ctx_for(loose), {}).status is Status.PASS

    def test_fail_names_column_and_object(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, rows=10, columns={"AMOUNT": col(nulls=0)}),
            live_orders=metrics(ORDERS_TARGET, rows=10, columns={"AMOUNT": col(nulls=5)}),
            orders_kw={"tolerance_pct": 0.0},
        )
        res = m_null_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "AMOUNT" in res.observed
        assert ORDERS_TARGET in res.observed

    def test_custom_sql_only_pass_does_not_overstate_coverage(self):
        """QC F13: same honesty rule as M-AGG-001."""
        res = m_null_001(ctx_for(_custom_sql_only_bundle()), {})
        assert res.status is Status.PASS
        assert "0 object(s) with column metrics" in res.observed


class TestMDist001:
    def test_no_keys_anywhere_skips(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET),
            live_orders=metrics(ORDERS_TARGET),
        )
        res = m_dist_001(ctx_for(bundle), {})
        assert res.status is Status.SKIP
        assert res.observed == "no keys declared in map"

    def test_distinct_drift_fails_naming_key(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, distinct={"ORDER_ID": 100}),
            live_orders=metrics(ORDERS_TARGET, distinct={"ORDER_ID": 90}),
            orders_kw={"keys": ("ORDER_ID",), "tolerance_pct": 0.01},
        )
        res = m_dist_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "ORDER_ID" in res.observed

    def test_within_tolerance_passes(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, distinct={"ORDER_ID": 100}),
            live_orders=metrics(ORDERS_TARGET, distinct={"ORDER_ID": 100}),
            orders_kw={"keys": ("ORDER_ID",)},
        )
        res = m_dist_001(ctx_for(bundle), {})
        assert res.status is Status.PASS

    def test_declared_key_unmeasured_on_one_side_warns(self):
        """QC F3b: a snapshot taken before the key was declared in the map
        must WARN with re-snapshot advice, never PASS."""
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET),  # no distinct_counts captured
            live_orders=metrics(ORDERS_TARGET, distinct={"ORDER_ID": 100}),
            orders_kw={"keys": ("ORDER_ID",)},
        )
        res = m_dist_001(ctx_for(bundle), {})
        assert res.status is Status.WARN
        assert "ORDER_ID" in res.observed
        assert "snapshot" in res.remediation
        assert "map" in res.remediation

    def test_breach_still_fails_when_another_key_is_unmeasured(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, distinct={"ORDER_ID": 100}),
            live_orders=metrics(ORDERS_TARGET, distinct={"ORDER_ID": 50}),
            orders_kw={"keys": ("ORDER_ID", "ITEM_ID"), "tolerance_pct": 0.0},
        )
        res = m_dist_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL


class TestMGrain001:
    GRAIN_KW = {"grain": ("REGION",), "tolerance_pct": 0.0}

    def test_no_grain_anywhere_skips(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET),
            live_orders=metrics(ORDERS_TARGET),
        )
        res = m_grain_001(ctx_for(bundle), {})
        assert res.status is Status.SKIP
        assert res.observed == "no grain declared in map"

    def test_missing_group_fails_with_topn_caveat(self):
        bundle = two_object_bundle(
            snap_orders=metrics(
                ORDERS_TARGET,
                grain=[
                    GrainGroup(group={"REGION": "EMEA"}, count=50),
                    GrainGroup(group={"REGION": "APAC"}, count=50),
                ],
            ),
            live_orders=metrics(
                ORDERS_TARGET, grain=[GrainGroup(group={"REGION": "EMEA"}, count=50)]
            ),
            orders_kw=self.GRAIN_KW,
        )
        res = m_grain_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "top-N" in res.observed
        assert "APAC" in res.observed
        assert any(r["issue"] == "missing in target" for r in res.evidence.sample_rows)

    def test_extra_group_and_count_mismatch_fail(self):
        bundle = two_object_bundle(
            snap_orders=metrics(
                ORDERS_TARGET, grain=[GrainGroup(group={"REGION": "EMEA"}, count=50)]
            ),
            live_orders=metrics(
                ORDERS_TARGET,
                grain=[
                    GrainGroup(group={"REGION": "EMEA"}, count=60),
                    GrainGroup(group={"REGION": "LATAM"}, count=5),
                ],
            ),
            orders_kw=self.GRAIN_KW,
        )
        res = m_grain_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        issues = {r["issue"] for r in res.evidence.sample_rows}
        assert issues == {"count mismatch", "extra in target"}

    def test_matching_groups_pass_with_caveat(self):
        groups = [GrainGroup(group={"REGION": "EMEA"}, count=50)]
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET, grain=list(groups)),
            live_orders=metrics(ORDERS_TARGET, grain=list(groups)),
            orders_kw=self.GRAIN_KW,
        )
        res = m_grain_001(ctx_for(bundle), {})
        assert res.status is Status.PASS
        assert "top-N" in res.observed

    def test_grain_declared_but_unmeasured_on_both_sides_warns(self):
        """QC F3b: grain declared in the map but absent from both sides'
        measurements (snapshot predates the declaration) must WARN with
        re-snapshot advice, never PASS."""
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET),
            live_orders=metrics(ORDERS_TARGET),
            orders_kw=self.GRAIN_KW,
        )
        res = m_grain_001(ctx_for(bundle), {})
        assert res.status is Status.WARN
        assert ORDERS_TARGET in res.observed
        assert "snapshot" in res.remediation
        assert "map" in res.remediation


# --- estate roll-up checks (PARITY-PLAN-V2 E7, S7.2) ----------------------


def run_result_with(verdict):
    from plumb.engine.models import Coverage, RunResult, Summary, utc_now

    return RunResult(
        run_id="t",
        timestamp=utc_now(),
        target=Target(type="parity", name="wb"),
        ruleset_version="1",
        verdict=verdict,
        coverage=Coverage(),
        summary=Summary(),
    )


def estate_entry(verdict=None, error=None, path="wb.twbx"):
    from plumb.parity.contracts import WorkbookParity

    return WorkbookParity(
        workbook_path=path,
        check_result=run_result_with(verdict) if verdict is not None else None,
        error=error,
    )


def estate_ctx(entries):
    from plumb.parity.contracts import ESTATE_EXTRAS_KEY, EstateResult

    estate = EstateResult(phase="check", entries=entries)
    return CheckContext(
        run_id="t",
        target=Target(type="parity", name="estate"),
        ruleset=Ruleset(version="1"),
        extras={ESTATE_EXTRAS_KEY: estate},
    )


class TestEstateChecks:
    def test_registration_and_catalog(self):
        import plumb.checks  # noqa: F401 - registration side effects
        from plumb.engine.models import CheckFamily, ExecutionType, Severity

        for check_id in ("M-ESTATE-001", "M-ESTATE-002"):
            definition = registry.get_check(check_id)
            assert definition.family is CheckFamily.MIGRATION_PARITY
            assert definition.execution_type is ExecutionType.STATIC
        assert registry.get_check("M-ESTATE-001").default_severity is Severity.BLOCKER
        assert registry.get_check("M-ESTATE-002").default_severity is Severity.HIGH

    @pytest.mark.parametrize("fn", ["m_estate_001", "m_estate_002"])
    def test_outside_estate_runs_skips(self, fn):
        import plumb.checks.parity as checks

        res = one(getattr(checks, fn)(ctx_for(extras={}), {}))
        assert res.status is Status.SKIP
        assert "no estate result" in res.observed
        res = one(
            getattr(checks, fn)(ctx_for(extras={"parity_estate": "not an estate"}), {})
        )
        assert res.status is Status.SKIP

    def test_estate_checks_emit_nothing_in_plain_parity_runs(self):
        """Cycle-2 fix: a single-workbook parity run carries a ParityBundle,
        not an EstateResult — the roll-up checks are meaningless there, not
        skipped risk, so they emit NOTHING instead of stamping 'no estate
        result' into every check/snapshot coverage caption."""
        from plumb.checks.parity import m_estate_001, m_estate_002

        bundle_ctx = ctx_for(make_bundle())
        assert m_estate_001(bundle_ctx, {}) == []
        assert m_estate_002(bundle_ctx, {}) == []

    @pytest.mark.parametrize("fn", ALL_CHECK_FNS)
    def test_workbook_checks_emit_nothing_in_estate_rollup_runs(self, fn):
        """Cycle-2 fix: in the estate roll-up run the per-workbook M-*
        checks already ran inside each sweep; emitting nine SKIPs would
        read as phantom capability gaps in the roll-up report."""
        from plumb.parity.contracts import ESTATE_EXTRAS_KEY, EstateResult

        ctx = ctx_for(extras={ESTATE_EXTRAS_KEY: EstateResult(phase="check")})
        assert fn(ctx, {}) == []

    def test_empty_estate_warns_never_passes(self):
        from plumb.checks.parity import m_estate_001, m_estate_002

        ctx = estate_ctx([])
        res1 = m_estate_001(ctx, {})
        assert res1.status is Status.WARN
        assert "empty estate" in res1.observed
        res2 = m_estate_002(ctx, {})
        assert res2.status is Status.SKIP

    def test_all_ready_both_pass(self):
        from plumb.checks.parity import m_estate_001, m_estate_002
        from plumb.engine.models import Verdict

        ctx = estate_ctx(
            [estate_entry(Verdict.READY, path=f"wb{i}.twbx") for i in range(3)]
        )
        assert m_estate_001(ctx, {}).status is Status.PASS
        assert m_estate_002(ctx, {}).status is Status.PASS

    def test_blocked_workbook_fails_001_named(self):
        from plumb.checks.parity import m_estate_001, m_estate_002
        from plumb.engine.models import Verdict

        ctx = estate_ctx(
            [
                estate_entry(Verdict.READY, path="ok.twbx"),
                estate_entry(Verdict.BLOCKED, path="bad.twbx"),
            ]
        )
        res = m_estate_001(ctx, {})
        assert res.status is Status.FAIL
        assert "bad.twbx" in res.observed
        assert "1 of 2" in res.observed
        # BLOCKED is 001's finding; 002 owns only the review tier.
        assert m_estate_002(ctx, {}).status is Status.PASS

    def test_errored_workbook_fails_001_with_cause(self):
        from plumb.checks.parity import m_estate_001

        ctx = estate_ctx(
            [
                estate_entry(error="unreadable workbook: boom", path="dead.twbx"),
                estate_entry(verdict=None, error=None, path="silent.twbx"),
            ]
        )
        res = m_estate_001(ctx, {})
        assert res.status is Status.FAIL
        assert "dead.twbx" in res.observed
        assert "unreadable workbook: boom" in res.observed
        assert "silent.twbx" in res.observed  # no result and no error: never a pass

    def test_review_workbook_fails_002_named(self):
        from plumb.checks.parity import m_estate_001, m_estate_002
        from plumb.engine.models import Verdict

        ctx = estate_ctx(
            [
                estate_entry(Verdict.READY, path="ok.twbx"),
                estate_entry(Verdict.REVIEW, path="drifty.twbx"),
            ]
        )
        assert m_estate_001(ctx, {}).status is Status.PASS
        res = m_estate_002(ctx, {})
        assert res.status is Status.FAIL
        assert "drifty.twbx" in res.observed

    def test_notes_workbook_warns_002(self):
        from plumb.checks.parity import m_estate_002
        from plumb.engine.models import Verdict

        ctx = estate_ctx([estate_entry(Verdict.READY_WITH_NOTES, path="noted.twbx")])
        res = m_estate_002(ctx, {})
        assert res.status is Status.WARN
        assert "noted.twbx" in res.observed

    @pytest.mark.parametrize(
        "verdicts,error_count",
        [
            (["READY"], 0),
            (["READY", "READY_WITH_NOTES"], 0),
            (["READY", "REVIEW"], 0),
            (["READY", "BLOCKED"], 0),
            (["READY"], 1),
            (["REVIEW", "READY_WITH_NOTES", "BLOCKED"], 1),
        ],
    )
    def test_engine_verdict_equals_d17_rollup(self, verdicts, error_count):
        """The design invariant (plan amendment): running M-ESTATE-001/002
        through compute_verdict reproduces EstateResult.compute_rollup()
        exactly, so the engine stays the only verdict logic."""
        from plumb.checks.parity import m_estate_001, m_estate_002
        from plumb.engine.models import Verdict
        from plumb.engine.verdict import compute_verdict
        from plumb.parity.contracts import ESTATE_EXTRAS_KEY

        entries = [
            estate_entry(Verdict(v), path=f"wb{i}.twbx") for i, v in enumerate(verdicts)
        ]
        entries += [
            estate_entry(error=f"boom {i}", path=f"err{i}.twbx")
            for i in range(error_count)
        ]
        ctx = estate_ctx(entries)
        estate = ctx.extras[ESTATE_EXTRAS_KEY]
        results = [m_estate_001(ctx, {}), m_estate_002(ctx, {})]
        assert compute_verdict(results) is estate.compute_rollup()


class TestMMap001PostSwap:
    def test_uninvertible_relations_fail_distinctly(self):
        resolution = MappingResolution(
            uninvertible=[(REL_ORDERS, "old name SALES.ORDERS is not fully qualified")]
        )
        bundle = make_bundle(resolution=resolution)
        bundle.post_swap = True
        res = m_map_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "post-swap inversion failed" in res.observed
        assert "not fully qualified" in res.observed
        assert "fully qualified" in res.remediation

    def test_unmapped_and_uninvertible_both_reported(self):
        resolution = MappingResolution(
            unmapped=[REL_ITEMS],
            uninvertible=[(REL_ORDERS, "ambiguous")],
        )
        bundle = make_bundle(resolution=resolution)
        bundle.post_swap = True
        res = m_map_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "unmapped" in res.observed
        assert "inversion failed" in res.observed

    def test_swapped_workbook_hint_on_unmapped_new_name(self):
        swapped = SourceRelation(
            datasource="Orders",
            kind="table",
            database="GALAXY",
            schema="PRES",
            table="FCT_ORDERS",
        )
        bundle = make_bundle(
            relations=[swapped],
            resolution=MappingResolution(unmapped=[swapped]),
        )
        bundle.map_new_fqns = frozenset({"GALAXY.PRES.FCT_ORDERS", "PRES.FCT_ORDERS"})
        res = m_map_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "--post-swap" in res.remediation

    def test_no_hint_when_already_post_swap(self):
        swapped = SourceRelation(
            datasource="Orders",
            kind="table",
            database="GALAXY",
            schema="PRES",
            table="FCT_ORDERS",
        )
        bundle = make_bundle(
            relations=[swapped],
            resolution=MappingResolution(unmapped=[swapped]),
        )
        bundle.map_new_fqns = frozenset({"GALAXY.PRES.FCT_ORDERS"})
        bundle.post_swap = True
        res = m_map_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "--post-swap" not in (res.remediation or "")


class TestMSnap001PostSwapHint:
    def test_missing_snapshot_on_new_name_suggests_post_swap(self):
        """Identity-resolved NEW names with missing snapshots are the
        forgot---post-swap signature: the remediation must warn against
        re-snapshotting the target side."""
        swapped = SourceRelation(
            datasource="Orders",
            kind="table",
            database="GALAXY",
            schema="PRES",
            table="FCT_ORDERS",
        )
        resolution = MappingResolution(
            resolved=[
                ResolvedObject(
                    relation=swapped,
                    target_fqn="GALAXY.PRES.FCT_ORDERS",
                    via_identity=True,
                )
            ]
        )
        bundle = make_bundle(relations=[swapped], resolution=resolution)
        bundle.map_new_fqns = frozenset({"GALAXY.PRES.FCT_ORDERS", "PRES.FCT_ORDERS"})
        res = m_snap_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "--post-swap" in res.remediation
        assert "re-snapshot" in res.remediation.lower()

    def test_missing_snapshot_without_new_name_keeps_snapshot_advice(self):
        resolution = MappingResolution(
            resolved=[ResolvedObject(relation=REL_ORDERS, target_fqn=ORDERS_TARGET)]
        )
        bundle = make_bundle(resolution=resolution)
        res = m_snap_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "plumb parity snapshot" in res.remediation


class TestRegisteredCallables:
    def test_registered_fn_is_the_check_function(self):
        """Pins a real bug: a helper defined between @register_check and the
        check function silently captures the registration, so the engine
        would invoke the helper. The registry must point at the checks."""
        import plumb.checks.parity as checks

        expected = {
            "M-SRC-001": checks.m_src_001,
            "M-MAP-001": checks.m_map_001,
            "M-SNAP-001": checks.m_snap_001,
            "M-SCHEMA-001": checks.m_schema_001,
            "M-ROW-001": checks.m_row_001,
            "M-AGG-001": checks.m_agg_001,
            "M-NULL-001": checks.m_null_001,
            "M-DIST-001": checks.m_dist_001,
            "M-GRAIN-001": checks.m_grain_001,
            "M-HASH-001": checks.m_hash_001,
            "M-ESTATE-001": checks.m_estate_001,
            "M-ESTATE-002": checks.m_estate_002,
        }
        for check_id, fn in expected.items():
            assert registry.get_check(check_id).fn is fn, check_id


class TestMSnap001PostSwapRemediation:
    def test_post_swap_missing_snapshot_never_advises_resnapshot(self):
        """QC F12: in post-swap mode the missing-snapshot remediation must
        tell the analyst to fix the map's old: spelling — re-snapshotting
        the swapped artifact would baseline the TARGET side."""
        resolution = MappingResolution(
            resolved=[ResolvedObject(relation=REL_ORDERS, target_fqn=ORDERS_TARGET)]
        )
        bundle = make_bundle(resolution=resolution)
        bundle.post_swap = True
        res = m_snap_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "plumb parity snapshot" not in res.remediation
        assert "old:" in res.remediation
        assert "Do NOT re-snapshot" in res.remediation


# --- M-HASH-001: row-hash deep compare (PARITY-PLAN-V2 item 6) -------------


def hashed(fqn, rows, hashes, cols=("AMOUNT", "REGION"), error=None):
    """A ParityMetrics carrying a row-hash window."""
    m = metrics(fqn, rows=rows)
    m.row_hashes = dict(hashes)
    m.hashed_columns = list(cols)
    m.hash_error = error
    return m


KEY_KW = {"keys": ("CUSTOMER_ID",)}
K1 = '{"CUSTOMER_ID": "C-101"}'
K2 = '{"CUSTOMER_ID": "C-205"}'


class TestMHash001:
    def test_skip_when_no_keys_declared(self):
        bundle = two_object_bundle(
            snap_orders=metrics(ORDERS_TARGET), live_orders=metrics(ORDERS_TARGET)
        )
        res = m_hash_001(ctx_for(bundle), {})
        assert res.status is Status.SKIP
        assert "no keys" in res.observed

    def test_identical_windows_pass(self):
        bundle = two_object_bundle(
            snap_orders=hashed(ORDERS_TARGET, 2, {K1: "111", K2: "222"}),
            live_orders=hashed(ORDERS_TARGET, 2, {K1: "111", K2: "222"}),
            orders_kw=dict(KEY_KW),
        )
        res = m_hash_001(ctx_for(bundle), {})
        assert res.status is Status.PASS
        assert "2 shared key(s)" in res.observed
        assert "capped" not in res.observed

    def test_cell_drift_on_shared_key_fails_naming_the_key(self):
        """The check's reason to exist: same row count, same distincts,
        but one row's contents changed - the hash is the only witness."""
        bundle = two_object_bundle(
            snap_orders=hashed(ORDERS_TARGET, 2, {K1: "111", K2: "222"}),
            live_orders=hashed(ORDERS_TARGET, 2, {K1: "111", K2: "999"}),
            orders_kw=dict(KEY_KW),
        )
        res = m_hash_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "C-205" in res.observed
        assert ORDERS_TARGET in res.observed
        (row,) = res.evidence.sample_rows
        assert row["legacy_hash"] == "222" and row["target_hash"] == "999"

    def test_capped_window_is_named_in_observed(self):
        bundle = two_object_bundle(
            snap_orders=hashed(ORDERS_TARGET, 5000, {K1: "111"}),
            live_orders=hashed(ORDERS_TARGET, 5000, {K1: "111"}),
            orders_kw=dict(KEY_KW),
        )
        res = m_hash_001(ctx_for(bundle), {})
        assert res.status is Status.PASS
        assert "capped window" in res.observed

    def test_pre_hash_snapshot_warns_with_resnapshot_advice(self):
        snap = metrics(ORDERS_TARGET, rows=2)  # no hashes, rows exist
        bundle = two_object_bundle(
            snap_orders=snap,
            live_orders=hashed(ORDERS_TARGET, 2, {K1: "111", K2: "222"}),
            orders_kw=dict(KEY_KW),
        )
        res = m_hash_001(ctx_for(bundle), {})
        assert res.status is Status.WARN
        assert "not captured" in res.observed
        assert "snapshot" in res.remediation

    def test_empty_table_with_no_hashes_is_fully_measured(self):
        """Zero rows means zero fingerprints - that is measurement, not a
        gap; the check must not WARN a legitimately empty object."""
        bundle = two_object_bundle(
            snap_orders=hashed(ORDERS_TARGET, 0, {}),
            live_orders=hashed(ORDERS_TARGET, 0, {}),
            orders_kw=dict(KEY_KW),
        )
        res = m_hash_001(ctx_for(bundle), {})
        assert res.status is Status.PASS
        assert "0 shared key(s)" in res.observed

    def test_different_hashed_column_sets_warn_never_fake_drift(self):
        """A schema change between snapshot and check makes every hash
        differ for a non-data reason; comparing them would manufacture
        drift. M-SCHEMA-001 owns the schema signal."""
        bundle = two_object_bundle(
            snap_orders=hashed(ORDERS_TARGET, 1, {K1: "111"}, cols=("AMOUNT",)),
            live_orders=hashed(ORDERS_TARGET, 1, {K1: "999"}, cols=("AMOUNT", "NEW_COL")),
            orders_kw=dict(KEY_KW),
        )
        res = m_hash_001(ctx_for(bundle), {})
        assert res.status is Status.WARN
        assert "column sets differ" in res.observed

    def test_window_membership_drift_warns_not_fails(self):
        """Keys present in only one window: deletion, insertion, or just
        window shift past the cap - M-ROW/M-DIST own count drift, so this
        names the drift without failing."""
        bundle = two_object_bundle(
            snap_orders=hashed(ORDERS_TARGET, 2, {K1: "111", K2: "222"}),
            live_orders=hashed(ORDERS_TARGET, 2, {K1: "111"}),
            orders_kw=dict(KEY_KW),
        )
        res = m_hash_001(ctx_for(bundle), {})
        assert res.status is Status.WARN
        assert "only in snapshot window" in res.observed
        assert "--hash-cap" in res.remediation

    def test_hash_error_is_error_never_pass(self):
        bundle = two_object_bundle(
            snap_orders=hashed(
                ORDERS_TARGET, 2, {}, error="declared key is not unique"
            ),
            live_orders=hashed(ORDERS_TARGET, 2, {K1: "111"}),
            orders_kw=dict(KEY_KW),
        )
        res = m_hash_001(ctx_for(bundle), {})
        assert res.status is Status.ERROR
        assert "not unique" in res.observed

    def test_drift_beats_membership_warn(self):
        """FAIL outranks WARN when both signals exist in one run."""
        bundle = two_object_bundle(
            snap_orders=hashed(ORDERS_TARGET, 3, {K1: "111", K2: "222"}),
            live_orders=hashed(ORDERS_TARGET, 3, {K1: "DRIFT"}),
            orders_kw=dict(KEY_KW),
        )
        res = m_hash_001(ctx_for(bundle), {})
        assert res.status is Status.FAIL
        assert "C-101" in res.observed

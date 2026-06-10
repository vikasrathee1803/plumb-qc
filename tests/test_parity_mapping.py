"""Tests for the parity object map: loading map.yml and resolving relations.

The invariants under test: a malformed map fails loudly with the file path
and reason; resolution is explicit and deterministic (explicit entry, then
ignore glob, then identity fallback, then unmapped) and never guesses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from plumb.config.loader import ConfigError
from plumb.parity.contracts import (
    REFUSAL_EXTRACT_ONLY,
    REFUSAL_JOIN,
    SourceRelation,
    snapshot_name,
)
from plumb.parity.mapping import (
    ParityMap,
    invert_map,
    load_map,
    parse_fqn,
    resolve,
    resolve_post_swap,
)
from plumb.parity.metrics import measure

VALID_MAP_YAML = """\
version: 1
defaults:
  tolerance_pct: 0.02
  identity_fallback: true
objects:
  - old: LEGACY_DB.SALES.ORDERS
    new: GALAXY_DB.PRESENTATION.FCT_ORDERS
    keys: [order_id]
    grain: [order_date, region]
    columns:
      region: sales_region
    tolerance_pct: 0.0
  - old: LEGACY_DB.SALES.CUSTOMERS
    new: GALAXY_DB.PRESENTATION.DIM_CUSTOMERS
ignore:
  - LEGACY_DB.SCRATCH.*
"""


def write_map(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "galaxy-map.yml"
    path.write_text(text, encoding="utf-8")
    return path


def make_map(**overrides: Any) -> ParityMap:
    data: dict[str, Any] = {"version": 1}
    data.update(overrides)
    return ParityMap.model_validate(data)


def table_relation(
    database: str | None = "LEGACY_DB",
    schema: str = "SALES",
    table: str = "ORDERS",
    datasource: str = "ds1",
) -> SourceRelation:
    return SourceRelation(
        datasource=datasource,
        kind="table",
        database=database,
        schema=schema,
        table=table,
    )


class TestLoadMap:
    def test_valid_full_map_loads(self, tmp_path: Path) -> None:
        parity_map = load_map(write_map(tmp_path, VALID_MAP_YAML))
        assert parity_map.version == 1
        assert parity_map.defaults.tolerance_pct == 0.02
        assert parity_map.defaults.identity_fallback is True
        assert len(parity_map.objects) == 2
        entry = parity_map.objects[0]
        assert entry.old == "LEGACY_DB.SALES.ORDERS"
        assert entry.new == "GALAXY_DB.PRESENTATION.FCT_ORDERS"
        assert entry.keys == ["ORDER_ID"]
        assert entry.grain == ["ORDER_DATE", "REGION"]
        assert entry.columns == {"REGION": "SALES_REGION"}
        assert entry.tolerance_pct == 0.0
        assert parity_map.objects[1].tolerance_pct is None
        assert parity_map.ignore == ["LEGACY_DB.SCRATCH.*"]

    def test_defaults_apply_for_minimal_map(self, tmp_path: Path) -> None:
        parity_map = load_map(write_map(tmp_path, "version: 1\n"))
        assert parity_map.defaults.tolerance_pct == 0.01
        assert parity_map.defaults.identity_fallback is True
        assert parity_map.objects == []
        assert parity_map.ignore == []

    def test_unknown_top_level_key_fails_with_path(self, tmp_path: Path) -> None:
        path = write_map(tmp_path, "version: 1\nobjectz: []\n")
        with pytest.raises(ConfigError) as excinfo:
            load_map(path)
        message = str(excinfo.value)
        assert str(path) in message
        assert "objectz" in message

    def test_unknown_nested_key_fails_with_path(self, tmp_path: Path) -> None:
        path = write_map(
            tmp_path,
            "version: 1\n"
            "objects:\n"
            "  - old: A.B.C\n"
            "    new: X.Y.Z\n"
            "    tolerance: 0.5\n",
        )
        with pytest.raises(ConfigError) as excinfo:
            load_map(path)
        message = str(excinfo.value)
        assert str(path) in message
        assert "tolerance" in message

    def test_unknown_defaults_key_fails(self, tmp_path: Path) -> None:
        path = write_map(tmp_path, "version: 1\ndefaults:\n  tolerance: 0.5\n")
        with pytest.raises(ConfigError, match="tolerance"):
            load_map(path)

    def test_bad_version_fails(self, tmp_path: Path) -> None:
        path = write_map(tmp_path, "version: 2\n")
        with pytest.raises(ConfigError) as excinfo:
            load_map(path)
        assert "version" in str(excinfo.value)

    def test_missing_version_fails(self, tmp_path: Path) -> None:
        path = write_map(tmp_path, "objects: []\n")
        with pytest.raises(ConfigError, match="version"):
            load_map(path)

    def test_duplicate_old_fails_case_insensitively(self, tmp_path: Path) -> None:
        path = write_map(
            tmp_path,
            "version: 1\n"
            "objects:\n"
            "  - old: LEGACY_DB.SALES.ORDERS\n"
            "    new: G.P.FCT_ORDERS\n"
            "  - old: legacy_db.sales.orders\n"
            "    new: G.P.FCT_ORDERS_V2\n",
        )
        with pytest.raises(ConfigError, match="duplicate old object"):
            load_map(path)

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.yml"
        with pytest.raises(ConfigError) as excinfo:
            load_map(missing)
        message = str(excinfo.value)
        assert "not found" in message
        assert str(missing) in message

    def test_invalid_yaml_fails(self, tmp_path: Path) -> None:
        path = write_map(tmp_path, "version: 1\nobjects: [unclosed\n")
        with pytest.raises(ConfigError, match="could not parse YAML"):
            load_map(path)

    def test_empty_file_fails(self, tmp_path: Path) -> None:
        path = write_map(tmp_path, "")
        with pytest.raises(ConfigError, match="empty"):
            load_map(path)

    def test_wrong_type_fails_with_path(self, tmp_path: Path) -> None:
        path = write_map(tmp_path, "version: 1\nobjects: not-a-list\n")
        with pytest.raises(ConfigError) as excinfo:
            load_map(path)
        assert str(path) in str(excinfo.value)

    def test_non_three_part_new_fails(self, tmp_path: Path) -> None:
        path = write_map(
            tmp_path,
            "version: 1\nobjects:\n  - old: A.B.C\n    new: PRESENTATION.FCT\n",
        )
        with pytest.raises(ConfigError, match="3-part"):
            load_map(path)

    def test_one_part_old_fails(self, tmp_path: Path) -> None:
        path = write_map(tmp_path, "version: 1\nobjects:\n  - old: ORDERS\n    new: A.B.C\n")
        with pytest.raises(ConfigError, match="2- or 3-part"):
            load_map(path)

    def test_tolerance_above_one_rejected_in_defaults(self, tmp_path: Path) -> None:
        """QC F14: tolerance_pct is a fraction; 5 (500%) must fail loud."""
        path = write_map(tmp_path, "version: 1\ndefaults:\n  tolerance_pct: 5\n")
        with pytest.raises(ConfigError, match="tolerance_pct"):
            load_map(path)

    def test_tolerance_above_one_rejected_per_object(self, tmp_path: Path) -> None:
        path = write_map(
            tmp_path,
            "version: 1\n"
            "objects:\n"
            "  - old: A.B.C\n"
            "    new: X.Y.Z\n"
            "    tolerance_pct: 5\n",
        )
        with pytest.raises(ConfigError, match="tolerance_pct"):
            load_map(path)

    def test_duplicate_new_column_names_rejected(self, tmp_path: Path) -> None:
        """QC F15: {A: X, B: X} maps two old columns onto one new column."""
        path = write_map(
            tmp_path,
            "version: 1\n"
            "objects:\n"
            "  - old: A.B.C\n"
            "    new: X.Y.Z\n"
            "    columns:\n"
            "      a: x\n"
            "      b: X\n",
        )
        with pytest.raises(ConfigError, match="duplicate column rename target"):
            load_map(path)


class TestParseFqn:
    def test_three_part_parses(self) -> None:
        assert parse_fqn("DB.SCHEMA.TABLE") == ("DB", "SCHEMA", "TABLE")

    @pytest.mark.parametrize("bad", ["SCHEMA.TABLE", "A.B.C.D", "TABLE", "A..C", ""])
    def test_non_three_part_raises(self, bad: str) -> None:
        with pytest.raises(ValueError, match="3-part"):
            parse_fqn(bad)


class TestResolve:
    def test_explicit_mapping_resolves_with_upper_cased_details(self) -> None:
        parity_map = make_map(
            objects=[
                {
                    "old": "legacy_db.sales.orders",
                    "new": "GALAXY_DB.PRESENTATION.FCT_ORDERS",
                    "keys": ["order_id"],
                    "grain": ["order_date", "region"],
                    "columns": {"region": "sales_region"},
                }
            ]
        )
        resolution = resolve([table_relation()], parity_map)
        assert resolution.unmapped == []
        assert resolution.ignored == []
        assert len(resolution.resolved) == 1
        resolved = resolution.resolved[0]
        assert resolved.target_fqn == "GALAXY_DB.PRESENTATION.FCT_ORDERS"
        assert resolved.via_identity is False
        assert resolved.keys == ("ORDER_ID",)
        assert resolved.grain == ("ORDER_DATE", "REGION")
        assert resolved.column_map == {"REGION": "SALES_REGION"}
        assert resolved.tolerance_pct == 0.01

    def test_identity_fallback_on(self) -> None:
        parity_map = make_map(defaults={"tolerance_pct": 0.05})
        relation = table_relation()
        resolution = resolve([relation], parity_map)
        assert resolution.unmapped == []
        assert len(resolution.resolved) == 1
        resolved = resolution.resolved[0]
        assert resolved.via_identity is True
        assert resolved.target_fqn == "LEGACY_DB.SALES.ORDERS"
        assert resolved.tolerance_pct == 0.05
        assert resolved.keys == ()
        assert resolved.grain == ()
        assert resolved.column_map == {}

    def test_identity_fallback_off_populates_unmapped(self) -> None:
        parity_map = make_map(defaults={"identity_fallback": False})
        relation = table_relation()
        resolution = resolve([relation], parity_map)
        assert resolution.resolved == []
        assert resolution.unmapped == [relation]

    def test_ignore_glob_routes_to_ignored_case_insensitively(self) -> None:
        parity_map = make_map(ignore=["LEGACY_DB.SCRATCH.*"])
        ignored = table_relation(database="legacy_db", schema="scratch", table="tmp_orders")
        kept = table_relation()
        resolution = resolve([ignored, kept], parity_map)
        assert resolution.ignored == [ignored]
        assert len(resolution.resolved) == 1
        assert resolution.resolved[0].relation is kept

    def test_explicit_entry_wins_over_ignore_glob(self) -> None:
        parity_map = make_map(
            objects=[{"old": "LEGACY_DB.SCRATCH.KEEP_ME", "new": "G.P.KEEP_ME"}],
            ignore=["LEGACY_DB.SCRATCH.*"],
        )
        relation = table_relation(schema="SCRATCH", table="KEEP_ME")
        resolution = resolve([relation], parity_map)
        assert resolution.ignored == []
        assert resolution.resolved[0].target_fqn == "G.P.KEEP_ME"

    def test_custom_sql_always_resolves_verbatim(self) -> None:
        parity_map = make_map(defaults={"identity_fallback": False, "tolerance_pct": 0.03})
        relation = SourceRelation(
            datasource="ds1", kind="custom_sql", custom_sql="SELECT 1 AS N"
        )
        resolution = resolve([relation], parity_map)
        assert resolution.unmapped == []
        assert len(resolution.resolved) == 1
        resolved = resolution.resolved[0]
        assert resolved.target_fqn == ""
        assert resolved.via_identity is True
        assert resolved.keys == ()
        assert resolved.grain == ()
        assert resolved.column_map == {}
        assert resolved.tolerance_pct == 0.03

    def test_refused_relations_appear_nowhere(self) -> None:
        parity_map = make_map(defaults={"identity_fallback": False})
        refused = [
            SourceRelation(datasource="ds1", kind="refused", refusal_reason=REFUSAL_JOIN),
            SourceRelation(
                datasource="ds2", kind="refused", refusal_reason=REFUSAL_EXTRACT_ONLY
            ),
        ]
        resolution = resolve(refused, parity_map)
        assert resolution.resolved == []
        assert resolution.unmapped == []
        assert resolution.ignored == []

    def test_two_part_relation_matches_three_part_old_by_trailing_parts(self) -> None:
        parity_map = make_map(
            objects=[{"old": "LEGACY_DB.SALES.ORDERS", "new": "G.P.FCT_ORDERS"}]
        )
        relation = table_relation(database=None)
        assert relation.fqn == "SALES.ORDERS"
        resolution = resolve([relation], parity_map)
        assert len(resolution.resolved) == 1
        assert resolution.resolved[0].target_fqn == "G.P.FCT_ORDERS"
        assert resolution.resolved[0].via_identity is False

    def test_two_part_old_matches_three_part_relation_by_trailing_parts(self) -> None:
        parity_map = make_map(objects=[{"old": "SALES.ORDERS", "new": "G.P.FCT_ORDERS"}])
        resolution = resolve([table_relation()], parity_map)
        assert len(resolution.resolved) == 1
        assert resolution.resolved[0].target_fqn == "G.P.FCT_ORDERS"

    def test_bare_table_never_tail_matches_multi_part_entry(self) -> None:
        """QC F7a: a 1-part name must not tail-match FINANCE.ORDERS."""
        parity_map = make_map(objects=[{"old": "FINANCE.ORDERS", "new": "G.P.FCT_ORDERS"}])
        relation = table_relation(database=None, schema=None, table="ORDERS")  # type: ignore[arg-type]
        assert relation.fqn == "ORDERS"
        resolution = resolve([relation], parity_map)
        assert resolution.resolved == []
        assert resolution.unmapped == [relation]

    def test_short_fqn_never_identity_resolves(self) -> None:
        """QC F7b: fewer than 3 parts cannot name the same object on both
        sides, so identity fallback must not apply."""
        parity_map = make_map()  # identity_fallback defaults to True
        relation = table_relation(database=None)
        assert relation.fqn == "SALES.ORDERS"
        resolution = resolve([relation], parity_map)
        assert resolution.resolved == []
        assert resolution.unmapped == [relation]

    def test_different_table_does_not_match(self) -> None:
        parity_map = make_map(
            defaults={"identity_fallback": False},
            objects=[{"old": "LEGACY_DB.SALES.ORDERS", "new": "G.P.FCT_ORDERS"}],
        )
        relation = table_relation(table="ORDER_ITEMS")
        resolution = resolve([relation], parity_map)
        assert resolution.resolved == []
        assert resolution.unmapped == [relation]

    def test_per_object_tolerance_overrides_default(self) -> None:
        parity_map = make_map(
            defaults={"tolerance_pct": 0.02},
            objects=[
                {"old": "LEGACY_DB.SALES.ORDERS", "new": "G.P.FCT_ORDERS", "tolerance_pct": 0.0},
                {"old": "LEGACY_DB.SALES.CUSTOMERS", "new": "G.P.DIM_CUSTOMERS"},
            ],
        )
        orders = table_relation()
        customers = table_relation(table="CUSTOMERS")
        resolution = resolve([orders, customers], parity_map)
        by_target = {r.target_fqn: r for r in resolution.resolved}
        assert by_target["G.P.FCT_ORDERS"].tolerance_pct == 0.0
        assert by_target["G.P.DIM_CUSTOMERS"].tolerance_pct == 0.02

    def test_empty_map_pure_identity(self) -> None:
        parity_map = make_map()
        relations = [table_relation(), table_relation(table="CUSTOMERS")]
        resolution = resolve(relations, parity_map)
        assert len(resolution.resolved) == 2
        assert all(r.via_identity for r in resolution.resolved)


# --- post-swap mode (PARITY-PLAN-V2 S8.1) -----------------------------------


def full_map() -> ParityMap:
    """A fully-qualified injective map exercising every invertible field."""
    return make_map(
        defaults={"tolerance_pct": 0.02, "identity_fallback": False},
        objects=[
            {
                "old": "LEGACY_DB.SALES.ORDERS",
                "new": "GALAXY_DB.PRESENTATION.FCT_ORDERS",
                "keys": ["order_id", "region"],
                "grain": ["order_date", "region"],
                "columns": {"region": "sales_region"},
                "tolerance_pct": 0.0,
            },
            {
                "old": "LEGACY_DB.SALES.CUSTOMERS",
                "new": "GALAXY_DB.PRESENTATION.DIM_CUSTOMERS",
            },
        ],
        ignore=["LEGACY_DB.SCRATCH.*"],
    )


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows


class FakeParitySession:
    """Records every statement verbatim. Answers column discovery with the
    canned columns, grain queries with no groups, and the aggregate with an
    empty row (measure() reads absent values as 0/None) — enough to prove
    two ResolvedObjects drive measure() to byte-identical SQL."""

    def __init__(self, columns: list[tuple[str, str]]) -> None:
        self._columns = columns
        self.statements: list[str] = []

    def execute(self, sql: str) -> _FakeResult:
        self.statements.append(sql)
        if "INFORMATION_SCHEMA" in sql:
            return _FakeResult(
                [{"COLUMN_NAME": name, "DATA_TYPE": dtype} for name, dtype in self._columns]
            )
        if "GROUP BY" in sql:
            return _FakeResult([])
        return _FakeResult([{}])


class TestInvertMap:
    def test_happy_path_inverts_every_field(self) -> None:
        inverted = invert_map(full_map())
        assert inverted.version == 1
        # defaults preserved verbatim
        assert inverted.defaults.tolerance_pct == 0.02
        assert inverted.defaults.identity_fallback is False
        first = inverted.objects[0]
        assert first.old == "GALAXY_DB.PRESENTATION.FCT_ORDERS"
        assert first.new == "LEGACY_DB.SALES.ORDERS"
        # keys/grain are relation-side names: the renamed REGION becomes
        # SALES_REGION (new-side), un-renamed names pass through.
        assert first.keys == ["ORDER_ID", "SALES_REGION"]
        assert first.grain == ["ORDER_DATE", "SALES_REGION"]
        assert first.columns == {"SALES_REGION": "REGION"}
        assert first.tolerance_pct == 0.0
        second = inverted.objects[1]
        assert second.old == "GALAXY_DB.PRESENTATION.DIM_CUSTOMERS"
        assert second.new == "LEGACY_DB.SALES.CUSTOMERS"
        assert second.tolerance_pct is None
        # ignore globs name old-side objects and cannot be translated.
        assert inverted.ignore == ["LEGACY_DB.SCRATCH.*"]

    def test_round_trip_is_identity(self) -> None:
        original = full_map()
        assert invert_map(invert_map(original)) == original

    def test_inverted_map_forward_resolves_a_new_named_relation(self) -> None:
        """The inverted map must behave correctly in a hypothetical forward
        resolve: relation-side (new) keys/grain, column_map back to legacy."""
        inverted = invert_map(full_map())
        relation = table_relation(
            database="GALAXY_DB", schema="PRESENTATION", table="FCT_ORDERS"
        )
        resolved = resolve([relation], inverted).resolved[0]
        assert resolved.target_fqn == "LEGACY_DB.SALES.ORDERS"
        assert resolved.column_map == {"SALES_REGION": "REGION"}
        assert resolved.keys == ("ORDER_ID", "SALES_REGION")
        assert resolved.grain == ("ORDER_DATE", "SALES_REGION")
        assert resolved.tolerance_pct == 0.0

    def test_two_part_old_refused_naming_entry(self) -> None:
        parity_map = make_map(
            objects=[{"old": "SALES.ORDERS", "new": "G.P.FCT_ORDERS"}]
        )
        with pytest.raises(ConfigError) as excinfo:
            invert_map(parity_map)
        message = str(excinfo.value)
        assert "SALES.ORDERS" in message
        assert "not fully qualified" in message

    def test_duplicate_new_refused_naming_collision_case_insensitively(self) -> None:
        parity_map = make_map(
            objects=[
                {"old": "L.S.ORDERS", "new": "G.P.FCT"},
                {"old": "L.S.ORDERS_ARCHIVE", "new": "g.p.fct"},
            ]
        )
        with pytest.raises(ConfigError) as excinfo:
            invert_map(parity_map)
        message = str(excinfo.value)
        assert "G.P.FCT" in message
        assert "L.S.ORDERS" in message
        assert "L.S.ORDERS_ARCHIVE" in message

    def test_every_offender_named_in_one_error(self) -> None:
        parity_map = make_map(
            objects=[
                {"old": "SALES.ORDERS", "new": "G.P.FCT_A"},
                {"old": "L.S.B", "new": "G.P.FCT_C"},
                {"old": "L.S.D", "new": "G.P.FCT_C"},
            ]
        )
        with pytest.raises(ConfigError) as excinfo:
            invert_map(parity_map)
        message = str(excinfo.value)
        assert "SALES.ORDERS" in message
        assert "G.P.FCT_C" in message

    def test_duplicate_new_map_loads_and_forward_resolves(self, tmp_path: Path) -> None:
        """Many-to-one maps are legal for forward checking (risk table row on
        non-injective maps); ONLY inversion refuses them."""
        path = write_map(
            tmp_path,
            "version: 1\n"
            "objects:\n"
            "  - old: LEGACY_DB.SALES.ORDERS\n"
            "    new: GALAXY_DB.P.FCT\n"
            "  - old: LEGACY_DB.SALES.ORDERS_ARCHIVE\n"
            "    new: GALAXY_DB.P.FCT\n",
        )
        parity_map = load_map(path)
        resolution = resolve([table_relation()], parity_map)
        assert resolution.resolved[0].target_fqn == "GALAXY_DB.P.FCT"
        with pytest.raises(ConfigError, match="GALAXY_DB.P.FCT"):
            invert_map(parity_map)


class TestResolvePostSwap:
    def test_core_invariant_round_trip(self) -> None:
        """Pre-swap resolve and post-swap resolve_post_swap must agree on
        everything that matters: the snapshot identity (so check finds the
        pre-swap snapshots) and the target-side measurement (byte-identical
        SQL against the NEW object, identical normalized metrics)."""
        parity_map = full_map()
        r_old = table_relation()  # LEGACY_DB.SALES.ORDERS, as pre-swap workbook spells it
        pre = resolve([r_old], parity_map).resolved[0]

        r_new = table_relation(
            database="GALAXY_DB", schema="PRESENTATION", table="FCT_ORDERS"
        )
        resolution = resolve_post_swap([r_new], parity_map)
        assert resolution.unmapped == []
        assert resolution.uninvertible == []
        post = resolution.resolved[0]

        # Snapshot identity: the synthetic legacy relation reproduces the
        # pre-swap snapshot name exactly.
        assert snapshot_name("parity__wb", post.relation) == snapshot_name(
            "parity__wb", r_old
        )
        # Same comparison contract as the pre-swap resolution.
        assert post.via_identity is False
        assert post.target_fqn == "GALAXY_DB.PRESENTATION.FCT_ORDERS"
        assert post.column_map == pre.column_map == {"REGION": "SALES_REGION"}
        assert post.keys == pre.keys == ("ORDER_ID", "REGION")
        assert post.grain == pre.grain == ("ORDER_DATE", "REGION")
        assert post.tolerance_pct == pre.tolerance_pct == 0.0

        # Target-side measurement is exactly what the v1 check phase runs.
        columns = [
            ("AMOUNT", "NUMBER"),
            ("ORDER_DATE", "DATE"),
            ("ORDER_ID", "NUMBER"),
            ("SALES_REGION", "TEXT"),
        ]
        pre_session = FakeParitySession(columns)
        post_session = FakeParitySession(columns)
        pre_metrics = measure(pre_session, pre, "target")
        post_metrics = measure(post_session, post, "target")
        assert pre_session.statements == post_session.statements
        assert post_metrics == pre_metrics

    def test_two_part_relation_fqn_tail_matches_entry_new(self) -> None:
        """QC F11: a 2-part relation FQN tail-matches, and the resolved
        target_fqn must be the entry's fully-qualified `new:` name — the
        relation's own 2-part spelling can never be measured (parse_fqn
        requires DB.SCHEMA.TABLE), which made every tail match a
        guaranteed measurement ERROR."""
        parity_map = full_map()
        relation = table_relation(
            database=None, schema="PRESENTATION", table="FCT_ORDERS"
        )
        assert relation.fqn == "PRESENTATION.FCT_ORDERS"
        resolution = resolve_post_swap([relation], parity_map)
        assert len(resolution.resolved) == 1
        resolved = resolution.resolved[0]
        # target_fqn is the authored `new:`, fully qualified and measurable.
        assert resolved.target_fqn == "GALAXY_DB.PRESENTATION.FCT_ORDERS"
        parse_fqn(resolved.target_fqn)  # must not raise
        # The legacy identity is still reconstructed fully qualified.
        assert resolved.relation.fqn == "LEGACY_DB.SALES.ORDERS"

    def test_two_part_tail_match_is_measurable_end_to_end(self) -> None:
        """QC F11 (the seam itself): the resolved object from a 2-part tail
        match must survive measure(..., 'target') — the original defect only
        appeared when mapping output met metrics input."""
        parity_map = full_map()
        relation = table_relation(
            database=None, schema="PRESENTATION", table="FCT_ORDERS"
        )
        resolved = resolve_post_swap([relation], parity_map).resolved[0]
        session = FakeParitySession(
            [
                ("AMOUNT", "NUMBER"),
                ("ORDER_DATE", "DATE"),
                ("ORDER_ID", "NUMBER"),
                ("SALES_REGION", "TEXT"),
            ]
        )
        metrics = measure(session, resolved, "target")
        assert metrics.object_fqn == "GALAXY_DB.PRESENTATION.FCT_ORDERS"

    def test_ambiguous_tail_match_refused_into_uninvertible(self) -> None:
        """QC F10: two entries with different databases share a SCHEMA.TABLE
        tail and pass the injectivity gate; a 2-part relation matches BOTH.
        First-match-wins would fabricate a legacy identity — the relation
        must land in uninvertible naming every candidate."""
        parity_map = make_map(
            objects=[
                {"old": "LDB1.S.T", "new": "GDB1.PRES.FCT"},
                {"old": "LDB2.S.T", "new": "GDB2.PRES.FCT"},
            ]
        )
        relation = table_relation(database=None, schema="PRES", table="FCT")
        resolution = resolve_post_swap([relation], parity_map)
        assert resolution.resolved == []
        assert resolution.unmapped == []
        assert len(resolution.uninvertible) == 1
        offender, reason = resolution.uninvertible[0]
        assert offender is relation
        assert "ambiguous" in reason
        assert "GDB1.PRES.FCT" in reason and "GDB2.PRES.FCT" in reason

    def test_three_part_relation_is_never_ambiguous_across_databases(self) -> None:
        """The F10 refusal must not over-trigger: a fully qualified relation
        FQN matches exactly one of the tail-sharing entries."""
        parity_map = make_map(
            objects=[
                {"old": "LDB1.S.T", "new": "GDB1.PRES.FCT"},
                {"old": "LDB2.S.T", "new": "GDB2.PRES.FCT"},
            ]
        )
        relation = table_relation(database="GDB2", schema="PRES", table="FCT")
        resolution = resolve_post_swap([relation], parity_map)
        assert resolution.uninvertible == []
        assert len(resolution.resolved) == 1
        assert resolution.resolved[0].relation.fqn == "LDB2.S.T"

    def test_bare_table_never_tail_matches_entry_new(self) -> None:
        parity_map = full_map()
        relation = table_relation(database=None, schema=None, table="FCT_ORDERS")  # type: ignore[arg-type]
        assert relation.fqn == "FCT_ORDERS"
        resolution = resolve_post_swap([relation], parity_map)
        assert resolution.resolved == []
        assert resolution.unmapped == [relation]

    def test_two_part_old_lands_in_uninvertible_with_reason(self) -> None:
        parity_map = make_map(
            objects=[{"old": "SALES.ORDERS", "new": "GALAXY_DB.PRESENTATION.FCT_ORDERS"}]
        )
        relation = table_relation(
            database="GALAXY_DB", schema="PRESENTATION", table="FCT_ORDERS"
        )
        resolution = resolve_post_swap([relation], parity_map)
        assert resolution.resolved == []
        assert resolution.unmapped == []  # NOT plain unmapped
        [(uninvertible_relation, reason)] = resolution.uninvertible
        assert uninvertible_relation is relation
        assert "SALES.ORDERS" in reason
        assert "not fully qualified" in reason

    def test_unmatched_three_part_uses_identity_fallback(self) -> None:
        parity_map = make_map()  # identity_fallback defaults to True
        relation = table_relation(database="GALAXY_DB", schema="P", table="UNTOUCHED")
        resolution = resolve_post_swap([relation], parity_map)
        assert len(resolution.resolved) == 1
        resolved = resolution.resolved[0]
        assert resolved.via_identity is True
        assert resolved.target_fqn == "GALAXY_DB.P.UNTOUCHED"
        assert resolved.relation is relation

    def test_unmatched_with_identity_disabled_is_unmapped(self) -> None:
        parity_map = make_map(defaults={"identity_fallback": False})
        relation = table_relation(database="GALAXY_DB", schema="P", table="UNTOUCHED")
        resolution = resolve_post_swap([relation], parity_map)
        assert resolution.resolved == []
        assert resolution.unmapped == [relation]

    def test_ignore_glob_applies_to_the_artifact_names(self) -> None:
        """Ignore patterns match the names in the artifact being checked —
        post-swap that means the NEW names."""
        parity_map = make_map(ignore=["GALAXY_DB.SCRATCH.*"])
        ignored = table_relation(database="galaxy_db", schema="scratch", table="tmp")
        kept = table_relation(database="GALAXY_DB", schema="P", table="KEPT")
        resolution = resolve_post_swap([ignored, kept], parity_map)
        assert resolution.ignored == [ignored]
        assert len(resolution.resolved) == 1
        assert resolution.resolved[0].relation is kept

    def test_refused_and_custom_sql_handled_as_v1(self) -> None:
        parity_map = make_map(defaults={"identity_fallback": False, "tolerance_pct": 0.03})
        refused = SourceRelation(
            datasource="ds1", kind="refused", refusal_reason=REFUSAL_JOIN
        )
        custom = SourceRelation(
            datasource="ds1", kind="custom_sql", custom_sql="SELECT 1 AS N"
        )
        resolution = resolve_post_swap([refused, custom], parity_map)
        assert len(resolution.resolved) == 1
        resolved = resolution.resolved[0]
        assert resolved.relation is custom
        assert resolved.target_fqn == ""
        assert resolved.via_identity is True
        assert resolved.tolerance_pct == 0.03
        assert resolution.unmapped == []
        assert resolution.ignored == []
        assert resolution.uninvertible == []

    def test_non_injective_map_raises_before_any_resolution(self) -> None:
        parity_map = make_map(
            objects=[
                {"old": "LEGACY_DB.SALES.ORDERS", "new": "GALAXY_DB.P.FCT"},
                {"old": "LEGACY_DB.SALES.ORDERS_V2", "new": "galaxy_db.p.fct"},
            ]
        )
        # Even with nothing to resolve, the authoring error is loud (D14).
        with pytest.raises(ConfigError) as excinfo:
            resolve_post_swap([], parity_map)
        message = str(excinfo.value)
        assert "GALAXY_DB.P.FCT" in message
        assert "LEGACY_DB.SALES.ORDERS" in message
        assert "LEGACY_DB.SALES.ORDERS_V2" in message

    def test_case_mismatch_in_entry_old_changes_snapshot_name(self) -> None:
        """Documents the case caveat: snapshot_name's hash is case-SENSITIVE
        over datasource|label, so an entry.old spelled differently from the
        pre-swap workbook reconstructs a snapshot name that does not exist
        (surfaces honestly as M-SNAP-001). Remediation: re-spell `old:` to
        match the pre-swap workbook exactly — do not re-snapshot."""
        parity_map = make_map(
            objects=[
                {"old": "LEGACY_DB.SALES.ORDERS", "new": "GALAXY_DB.PRESENTATION.FCT_ORDERS"}
            ]
        )
        # The pre-swap workbook spelled the database lower-case; forward
        # matching is case-insensitive, so snapshot mode accepted it.
        pre_swap = table_relation(database="legacy_db")
        assert resolve([pre_swap], parity_map).resolved[0].via_identity is False
        r_new = table_relation(
            database="GALAXY_DB", schema="PRESENTATION", table="FCT_ORDERS"
        )
        post = resolve_post_swap([r_new], parity_map).resolved[0]
        assert snapshot_name("p", post.relation) != snapshot_name("p", pre_swap)

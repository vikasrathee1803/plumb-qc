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
)
from plumb.parity.mapping import ParityMap, load_map, parse_fqn, resolve

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

"""Tests for the estate runner (PARITY-PLAN-V2 S7.1).

Manifest loading (YAML resolution, glob sugar, loud ConfigErrors) and
run_estate semantics: sequential sweeps with one session each (D12),
snapshot-before-check ordering in phase "run" (D16), per-workbook error
isolation that never aborts the estate, D17 roll-up math, and the shared
run_id audit story. The M-ESTATE-* checks are covered elsewhere; these
tests assert on the EstateResult itself, never on the roll-up run's
verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from plumb.baseline.store import LocalParquetStore
from plumb.config.loader import ConfigError
from plumb.config.models import Ruleset
from plumb.engine.models import Coverage, RunResult, Summary, Target, Verdict, utc_now
from plumb.parity.contracts import EstateResult, WorkbookParity
from plumb.parity.estate import EstateManifest, WorkbookEntry, load_manifest, run_estate
from tests._fakes import RouteSession
from tests._parity_fixtures import TWB_CUSTOM_SQL, TWB_MALFORMED, write_twb

M_IDS = [
    "M-SRC-001",
    "M-MAP-001",
    "M-SNAP-001",
    "M-SCHEMA-001",
    "M-ROW-001",
    "M-AGG-001",
    "M-NULL-001",
    "M-DIST-001",
    "M-GRAIN-001",
]

COUNT_ROUTES: list[tuple[str, list[dict[str, Any]]]] = [
    ("SELECT COUNT(*)", [{"ROW_COUNT": 42}])
]


def parity_ruleset() -> Ruleset:
    return Ruleset.model_validate(
        {"version": "test", "checks": [{"id": cid, "enabled": True} for cid in M_IDS]}
    )


def manifest_for(workbooks: list[Path]) -> EstateManifest:
    return EstateManifest(
        version=1, workbooks=[WorkbookEntry(path=str(wb)) for wb in workbooks]
    )


def tracked_factory(
    label: str,
    events: list[str],
    routes: list[tuple[str, list[dict[str, Any]]]],
) -> Callable[[], Any]:
    """A session factory that records open/exec/close events so tests can
    assert the D12 lifecycle: one open per sweep, all of one side's
    queries before the other side opens, always closed."""

    def factory() -> Any:
        events.append(f"open:{label}")
        session = RouteSession(routes=list(routes))
        inner_execute = session.execute
        inner_close = session.close

        def execute(sql: str, params: Any = None) -> Any:
            events.append(f"exec:{label}")
            return inner_execute(sql, params)

        def close() -> None:
            events.append(f"close:{label}")
            inner_close()

        session.execute = execute  # type: ignore[method-assign]
        session.close = close  # type: ignore[method-assign]
        return session

    return factory


def fake_run(verdict: Verdict) -> RunResult:
    return RunResult(
        run_id="run-1",
        timestamp=utc_now(),
        target=Target(type="parity", name="wb"),
        ruleset_version="test",
        verdict=verdict,
        coverage=Coverage(),
        summary=Summary(),
    )


class TestLoadManifest:
    def test_yaml_paths_resolve_relative_to_manifest_dir(self, tmp_path: Path) -> None:
        wave = tmp_path / "wave"
        wave.mkdir()
        manifest_file = wave / "manifest.yml"
        manifest_file.write_text(
            "version: 1\n"
            "workbooks:\n"
            "  - path: wb/kpi.twb\n"
            "    map: maps/kpi-map.yml\n"
            "  - path: wb/sales.twb\n"
            "    snapshot_prefix: parity__sales_wave1\n",
            encoding="utf-8",
        )
        manifest = load_manifest(manifest_file)
        assert manifest.source_ref == str(manifest_file)
        first, second = manifest.workbooks
        assert first.path == str(wave / "wb" / "kpi.twb")
        assert first.map == str(wave / "maps" / "kpi-map.yml")
        assert first.snapshot_prefix is None
        assert second.path == str(wave / "wb" / "sales.twb")
        assert second.map is None
        assert second.snapshot_prefix == "parity__sales_wave1"

    def test_yaml_absolute_paths_kept_and_default_map_fills_gaps(
        self, tmp_path: Path
    ) -> None:
        abs_wb = tmp_path / "elsewhere" / "abs.twb"
        manifest_file = tmp_path / "manifest.yml"
        manifest_file.write_text(
            "version: 1\n"
            "workbooks:\n"
            f"  - path: '{abs_wb}'\n"
            "  - path: rel.twb\n"
            "    map: own-map.yml\n",
            encoding="utf-8",
        )
        manifest = load_manifest(manifest_file, default_map=Path("default-map.yml"))
        first, second = manifest.workbooks
        assert first.path == str(abs_wb)
        # Entry without a map falls back to the default; a per-entry map
        # wins and resolves relative to the manifest dir.
        assert first.map == "default-map.yml"
        assert second.map == str(tmp_path / "own-map.yml")

    def test_glob_expands_sorted_with_default_map(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_twb(tmp_path, TWB_CUSTOM_SQL, "b.twb")
        write_twb(tmp_path, TWB_CUSTOM_SQL, "a.twb")
        monkeypatch.chdir(tmp_path)
        manifest = load_manifest("*.twb", default_map=Path("map.yml"))
        assert [entry.path for entry in manifest.workbooks] == ["a.twb", "b.twb"]
        assert all(entry.map == "map.yml" for entry in manifest.workbooks)
        assert all(entry.snapshot_prefix is None for entry in manifest.workbooks)
        assert manifest.source_ref == "*.twb"

    def test_glob_zero_matches_is_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError) as exc_info:
            load_manifest("no-such-*.twb")
        assert "no-such-*.twb" in str(exc_info.value)

    def test_empty_workbooks_list_is_config_error(self, tmp_path: Path) -> None:
        manifest_file = tmp_path / "manifest.yml"
        manifest_file.write_text("version: 1\nworkbooks: []\n", encoding="utf-8")
        with pytest.raises(ConfigError) as exc_info:
            load_manifest(manifest_file)
        assert str(manifest_file) in str(exc_info.value)

    def test_unknown_keys_are_config_errors(self, tmp_path: Path) -> None:
        top_level = tmp_path / "top.yml"
        top_level.write_text(
            "version: 1\nworkbooks:\n  - path: a.twb\nnonsense: true\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError):
            load_manifest(top_level)
        entry_level = tmp_path / "entry.yml"
        entry_level.write_text(
            "version: 1\nworkbooks:\n  - path: a.twb\n    nonsense: true\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError):
            load_manifest(entry_level)

    def test_duplicate_snapshot_prefix_is_config_error(self, tmp_path: Path) -> None:
        manifest_file = tmp_path / "manifest.yml"
        manifest_file.write_text(
            "version: 1\n"
            "workbooks:\n"
            "  - path: wave1/sales.twb\n"
            "  - path: wave2/sales.twb\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError) as exc_info:
            load_manifest(manifest_file)
        message = str(exc_info.value)
        assert str(tmp_path / "wave1" / "sales.twb") in message
        assert str(tmp_path / "wave2" / "sales.twb") in message
        assert "parity__sales" in message

    def test_duplicate_prefix_via_glob_two_dirs_same_stem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "wave1").mkdir()
        (tmp_path / "wave2").mkdir()
        write_twb(tmp_path / "wave1", TWB_CUSTOM_SQL, "sales.twb")
        write_twb(tmp_path / "wave2", TWB_CUSTOM_SQL, "sales.twb")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ConfigError) as exc_info:
            load_manifest("wave*/sales.twb")
        message = str(exc_info.value)
        assert "wave1" in message and "wave2" in message

    def test_snapshot_prefix_override_resolves_collision_at_load(
        self, tmp_path: Path
    ) -> None:
        manifest_file = tmp_path / "manifest.yml"
        manifest_file.write_text(
            "version: 1\n"
            "workbooks:\n"
            "  - path: wave1/sales.twb\n"
            "  - path: wave2/sales.twb\n"
            "    snapshot_prefix: parity__sales_wave2\n",
            encoding="utf-8",
        )
        manifest = load_manifest(manifest_file)
        assert len(manifest.workbooks) == 2


class TestRunEstate:
    def test_three_workbook_run_produces_rollup(self, tmp_path: Path) -> None:
        for name in ("kpi1.twb", "kpi2.twb", "kpi3.twb"):
            write_twb(tmp_path, TWB_CUSTOM_SQL, name)
        manifest_file = tmp_path / "wave1.yml"
        manifest_file.write_text(
            "version: 1\n"
            "workbooks:\n"
            "  - path: kpi1.twb\n"
            "  - path: kpi2.twb\n"
            "  - path: kpi3.twb\n",
            encoding="utf-8",
        )
        manifest = load_manifest(manifest_file)
        events: list[str] = []
        estate, rollup_run = run_estate(
            manifest,
            "run",
            ruleset=parity_ruleset(),
            store=LocalParquetStore(tmp_path / "store"),
            legacy_session_factory=tracked_factory("legacy", events, COUNT_ROUTES),
            target_session_factory=tracked_factory("target", events, COUNT_ROUTES),
            run_id="wave-1",
        )
        assert estate.phase == "run"
        assert estate.manifest_ref == str(manifest_file)
        assert estate.created_at
        assert len(estate.entries) == 3
        for entry in estate.entries:
            assert entry.error is None
            assert entry.snapshot_result is not None
            assert entry.check_result is not None
            # One run_id for the whole wave (D12 audit story).
            assert entry.snapshot_result.run_id == "wave-1"
            assert entry.check_result.run_id == "wave-1"
        assert estate.rollup in (Verdict.READY, Verdict.READY_WITH_NOTES)
        assert estate.rollup == estate.compute_rollup()
        assert rollup_run.run_id == "wave-1"
        assert rollup_run.target.name == "wave1"
        assert rollup_run.target.source_ref == str(manifest_file)

    def test_one_blocked_workbook_blocks_the_estate(self, tmp_path: Path) -> None:
        wbs = [write_twb(tmp_path, TWB_CUSTOM_SQL, f"kpi{i}.twb") for i in (1, 2, 3)]
        store = LocalParquetStore(tmp_path / "store")
        run_estate(
            manifest_for(wbs[:2]),
            "snapshot",
            ruleset=parity_ruleset(),
            store=store,
            legacy_session_factory=lambda: RouteSession(routes=list(COUNT_ROUTES)),
        )
        # kpi3 was never snapshotted -> M-SNAP-001 FAIL -> BLOCKED.
        estate, _ = run_estate(
            manifest_for(wbs),
            "check",
            ruleset=parity_ruleset(),
            store=store,
            target_session_factory=lambda: RouteSession(routes=list(COUNT_ROUTES)),
        )
        blocked = estate.entries[2]
        assert blocked.check_result is not None
        assert blocked.check_result.verdict is Verdict.BLOCKED
        for entry in estate.entries[:2]:
            assert entry.check_result is not None
            assert entry.check_result.verdict is not Verdict.BLOCKED
        assert estate.rollup is Verdict.BLOCKED

    def test_unreadable_workbook_never_aborts_the_estate(self, tmp_path: Path) -> None:
        good1 = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi1.twb")
        bad = write_twb(tmp_path, TWB_MALFORMED, "bad.twb")
        good2 = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi3.twb")
        events: list[str] = []
        estate, _ = run_estate(
            manifest_for([good1, bad, good2]),
            "snapshot",
            ruleset=parity_ruleset(),
            store=LocalParquetStore(tmp_path / "store"),
            legacy_session_factory=tracked_factory("legacy", events, COUNT_ROUTES),
        )
        assert estate.entries[1].error
        assert estate.entries[1].snapshot_result is None
        # The other workbooks still ran (S7.1 AC: errors never abort).
        assert estate.entries[0].snapshot_result is not None
        assert estate.entries[2].snapshot_result is not None
        assert estate.rollup is Verdict.BLOCKED
        # Factory called exactly once; session closed despite the error.
        assert events.count("open:legacy") == 1
        assert events[-1] == "close:legacy"

    def test_run_phase_snapshots_all_before_any_check(self, tmp_path: Path) -> None:
        wbs = [write_twb(tmp_path, TWB_CUSTOM_SQL, f"kpi{i}.twb") for i in (1, 2)]
        events: list[str] = []
        estate, _ = run_estate(
            manifest_for(wbs),
            "run",
            ruleset=parity_ruleset(),
            store=LocalParquetStore(tmp_path / "store"),
            legacy_session_factory=tracked_factory("legacy", events, COUNT_ROUTES),
            target_session_factory=tracked_factory("target", events, COUNT_ROUTES),
        )
        assert events.count("open:legacy") == 1
        assert events.count("open:target") == 1
        # Legacy closed before target opens: never two sessions at once.
        assert events.index("close:legacy") < events.index("open:target")
        legacy_execs = [i for i, e in enumerate(events) if e == "exec:legacy"]
        target_execs = [i for i, e in enumerate(events) if e == "exec:target"]
        assert legacy_execs and target_execs
        assert max(legacy_execs) < min(target_execs)
        assert events[-1] == "close:target"
        for entry in estate.entries:
            assert entry.snapshot_result is not None
            assert entry.check_result is not None

    def test_snapshot_sweep_error_skips_check_sweep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import plumb.parity.estate as estate_mod

        calls: list[tuple[str, str]] = []
        real_run_parity = estate_mod.run_parity

        def spy(*, workbook: Path, mode: str, **kwargs: Any) -> Any:
            calls.append((workbook.name, mode))
            return real_run_parity(workbook=workbook, mode=mode, **kwargs)

        monkeypatch.setattr(estate_mod, "run_parity", spy)
        good = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        bad = write_twb(tmp_path, TWB_MALFORMED, "bad.twb")
        estate, _ = run_estate(
            manifest_for([bad, good]),
            "run",
            ruleset=parity_ruleset(),
            store=LocalParquetStore(tmp_path / "store"),
            legacy_session_factory=lambda: RouteSession(routes=list(COUNT_ROUTES)),
            target_session_factory=lambda: RouteSession(routes=list(COUNT_ROUTES)),
        )
        assert estate.entries[0].error
        assert estate.entries[0].check_result is None
        assert ("bad.twb", "snapshot") in calls
        assert ("bad.twb", "check") not in calls
        assert ("kpi.twb", "snapshot") in calls
        assert ("kpi.twb", "check") in calls

    def test_static_only_run_with_no_factories(self, tmp_path: Path) -> None:
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        estate, rollup_run = run_estate(
            manifest_for([wb]),
            "run",
            ruleset=parity_ruleset(),
            store=LocalParquetStore(tmp_path / "store"),
        )
        entry = estate.entries[0]
        assert entry.error is None
        assert entry.snapshot_result is not None
        assert entry.check_result is not None
        assert estate.rollup is not None
        # Manifest built in code carries no source ref.
        assert rollup_run.target.name == "estate"
        assert rollup_run.target.source_ref is None

    def test_post_swap_is_check_phase_only(self, tmp_path: Path) -> None:
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        events: list[str] = []
        for phase in ("snapshot", "run"):
            with pytest.raises(ValueError):
                run_estate(
                    manifest_for([wb]),
                    phase,  # type: ignore[arg-type]
                    ruleset=parity_ruleset(),
                    store=LocalParquetStore(tmp_path / "store"),
                    legacy_session_factory=tracked_factory("legacy", events, COUNT_ROUTES),
                    target_session_factory=tracked_factory("target", events, COUNT_ROUTES),
                    post_swap=True,
                )
        # The guard fires before any session is opened.
        assert events == []

    def test_shared_run_id_generated_when_absent(self, tmp_path: Path) -> None:
        wbs = [write_twb(tmp_path, TWB_CUSTOM_SQL, f"kpi{i}.twb") for i in (1, 2)]
        estate, rollup_run = run_estate(
            manifest_for(wbs),
            "snapshot",
            ruleset=parity_ruleset(),
            store=LocalParquetStore(tmp_path / "store"),
            legacy_session_factory=lambda: RouteSession(routes=list(COUNT_ROUTES)),
        )
        first = estate.entries[0].snapshot_result
        second = estate.entries[1].snapshot_result
        assert first is not None and second is not None
        assert first.run_id == second.run_id == rollup_run.run_id

    def test_snapshot_prefix_override_is_honored_end_to_end(
        self, tmp_path: Path
    ) -> None:
        """The manifest's snapshot_prefix override (D13: disambiguates two
        same-stem workbooks) flows through run_parity into the store: the
        snapshot is written under the OVERRIDE prefix, and a later check
        with the same manifest finds it there."""
        wb = write_twb(tmp_path, TWB_CUSTOM_SQL, "kpi.twb")
        store = LocalParquetStore(tmp_path / "store")
        manifest = EstateManifest(
            version=1,
            workbooks=[WorkbookEntry(path=str(wb), snapshot_prefix="parity__kpi_wave2")],
        )
        estate, _ = run_estate(
            manifest,
            "snapshot",
            ruleset=parity_ruleset(),
            store=store,
            legacy_session_factory=lambda: RouteSession(routes=list(COUNT_ROUTES)),
        )
        entry = estate.entries[0]
        assert entry.error is None
        assert entry.snapshot_result is not None
        names = store.list_names()
        assert names and all(n.startswith("parity__kpi_wave2__") for n in names)
        estate_check, _ = run_estate(
            manifest,
            "check",
            ruleset=parity_ruleset(),
            store=store,
            target_session_factory=lambda: RouteSession(routes=list(COUNT_ROUTES)),
        )
        assert estate_check.rollup is Verdict.READY


class TestComputeRollup:
    def test_all_ready_is_ready(self) -> None:
        estate = EstateResult(
            phase="check",
            entries=[
                WorkbookParity(workbook_path="a", check_result=fake_run(Verdict.READY)),
                WorkbookParity(workbook_path="b", check_result=fake_run(Verdict.READY)),
            ],
        )
        assert estate.compute_rollup() is Verdict.READY

    def test_any_notes_is_ready_with_notes(self) -> None:
        estate = EstateResult(
            phase="check",
            entries=[
                WorkbookParity(workbook_path="a", check_result=fake_run(Verdict.READY)),
                WorkbookParity(
                    workbook_path="b", check_result=fake_run(Verdict.READY_WITH_NOTES)
                ),
            ],
        )
        assert estate.compute_rollup() is Verdict.READY_WITH_NOTES

    def test_any_review_is_review(self) -> None:
        estate = EstateResult(
            phase="check",
            entries=[
                WorkbookParity(
                    workbook_path="a", check_result=fake_run(Verdict.READY_WITH_NOTES)
                ),
                WorkbookParity(workbook_path="b", check_result=fake_run(Verdict.REVIEW)),
            ],
        )
        assert estate.compute_rollup() is Verdict.REVIEW

    def test_any_blocked_is_blocked(self) -> None:
        estate = EstateResult(
            phase="check",
            entries=[
                WorkbookParity(workbook_path="a", check_result=fake_run(Verdict.REVIEW)),
                WorkbookParity(workbook_path="b", check_result=fake_run(Verdict.BLOCKED)),
            ],
        )
        assert estate.compute_rollup() is Verdict.BLOCKED

    def test_errored_entry_counts_as_blocked(self) -> None:
        estate = EstateResult(
            phase="check",
            entries=[
                WorkbookParity(workbook_path="a", check_result=fake_run(Verdict.READY)),
                WorkbookParity(workbook_path="b", error="could not parse"),
            ],
        )
        assert estate.compute_rollup() is Verdict.BLOCKED

    def test_resultless_entry_counts_as_blocked(self) -> None:
        estate = EstateResult(
            phase="check",
            entries=[WorkbookParity(workbook_path="a")],
        )
        assert estate.compute_rollup() is Verdict.BLOCKED

    def test_empty_estate_is_none(self) -> None:
        assert EstateResult(phase="check").compute_rollup() is None

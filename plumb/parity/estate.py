"""Estate runner: one manifest in, a whole migration wave proven (E7).

v1 proves one workbook per invocation; a migration wave is N workbooks.
This module loads an estate manifest - an explicit YAML list, or a glob
pattern that expands to identity-map entries (PARITY-PLAN-V2 D13) - and
sweeps the existing single-workbook pipeline (parity/runner.run_parity)
over every entry, assembling the EstateResult that the M-ESTATE-* checks
and the estate report writers consume. Two stances are load-bearing:

- D12: sweeps are sequential and share one session. A session factory is
  called at most once per sweep and the session is always closed at sweep
  end; in phase "run" the legacy session is closed before the target
  session is opened, so two warehouse sessions are never open at once and
  the audit QUERY_TAG story (one plumb_qc:{run_id} per wave) stays
  unambiguous.
- S7.1: per-workbook failures never abort the estate. A workbook that
  cannot run lands on its entry's error and the sweep moves on; the
  roll-up counts it as BLOCKED instead of letting it vanish.
"""

from __future__ import annotations

import glob as glob_module
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from plumb.baseline.store import BaselineStore
from plumb.checks._tableau import TableauParseError
from plumb.config.loader import ConfigError, load_yaml_mapping
from plumb.config.models import Ruleset
from plumb.engine.models import RunResult, Target, utc_now
from plumb.engine.runner import RunRequest, run_checks
from plumb.parity.contracts import (
    ESTATE_EXTRAS_KEY,
    EstateResult,
    ParityMode,
    ParityPhase,
    WorkbookParity,
    snapshot_prefix_for,
)
from plumb.parity.runner import run_parity

SessionFactory = Callable[[], Any]


class WorkbookEntry(BaseModel):
    """One workbook in the estate manifest.

    snapshot_prefix exists so two same-stem workbooks can be declared
    without silently sharing snapshots; it participates in the collision
    validation in load_manifest and is passed through to run_parity, which
    otherwise derives the prefix from the workbook path. The override must
    be identical across the snapshot and check phases (same manifest)."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    map: str | None = None
    snapshot_prefix: str | None = None


class EstateManifest(BaseModel):
    """The validated content of an estate manifest.

    Paths are stored resolved: YAML entries relative to the manifest
    file's directory, glob matches as the glob produced them (relative to
    the cwd). An empty workbook list is a loud ConfigError at load time -
    an estate of nothing can only mean a wrong manifest."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    workbooks: list[WorkbookEntry] = Field(min_length=1)

    _source_ref: str | None = PrivateAttr(default=None)

    @property
    def source_ref(self) -> str | None:
        """Where this manifest came from (file path or glob pattern); set
        by load_manifest, None for manifests constructed in code."""
        return self._source_ref


def _format_validation_error(source: Path | str, exc: ValidationError) -> str:
    lines = [f"invalid estate manifest in {source}:"]
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "(root)"
        lines.append(f"  {location}: {err['msg']}")
    return "\n".join(lines)


def load_manifest(spec: str | Path, *, default_map: Path | None = None) -> EstateManifest:
    """Load an estate manifest from a YAML file or expand a glob (D13).

    An existing .yml/.yaml file is loaded as an explicit manifest; its
    workbook and map paths resolve relative to the manifest file's
    directory. Anything else is treated as a glob pattern relative to the
    cwd; every match becomes an identity-map entry. default_map fills the
    map of any entry that does not declare its own (per-entry map wins).
    Zero glob matches, unknown keys, an empty workbook list, and two
    entries whose effective snapshot prefixes collide all raise ConfigError
    naming the file/pattern and the reason.
    """
    spec_path = Path(spec)
    if spec_path.suffix.lower() in (".yml", ".yaml") and spec_path.is_file():
        manifest = _load_yaml_manifest(spec_path, default_map)
    else:
        manifest = _expand_glob(str(spec), default_map)
    _ensure_unique_prefixes(manifest, str(spec))
    manifest._source_ref = str(spec)
    return manifest


def _load_yaml_manifest(path: Path, default_map: Path | None) -> EstateManifest:
    raw = load_yaml_mapping(path)
    try:
        manifest = EstateManifest.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc
    base = path.parent
    # base / p leaves an already-absolute p untouched (pathlib semantics),
    # so absolute manifest entries pass through unchanged.
    manifest.workbooks = [
        entry.model_copy(
            update={
                "path": str(base / entry.path),
                "map": (
                    str(base / entry.map)
                    if entry.map is not None
                    else (str(default_map) if default_map is not None else None)
                ),
            }
        )
        for entry in manifest.workbooks
    ]
    return manifest


def _expand_glob(pattern: str, default_map: Path | None) -> EstateManifest:
    matches = sorted(glob_module.glob(pattern, recursive=True))
    if not matches:
        raise ConfigError(
            f"estate spec {pattern!r} matched no workbooks: it is not an "
            "existing manifest file and the glob expanded to nothing"
        )
    map_str = str(default_map) if default_map is not None else None
    return EstateManifest(
        version=1,
        workbooks=[WorkbookEntry(path=match, map=map_str) for match in matches],
    )


def _ensure_unique_prefixes(manifest: EstateManifest, source: str) -> None:
    """Two entries with the same effective snapshot prefix would silently
    overwrite each other's snapshots (the store is keyed by prefix-derived
    names); refuse the manifest loudly instead."""
    by_prefix: dict[str, list[str]] = {}
    for entry in manifest.workbooks:
        prefix = entry.snapshot_prefix or snapshot_prefix_for(entry.path)
        by_prefix.setdefault(prefix, []).append(entry.path)
    collisions = {p: paths for p, paths in by_prefix.items() if len(paths) > 1}
    if not collisions:
        return
    lines = [f"estate manifest {source}: workbooks would share a snapshot prefix"]
    for prefix in sorted(collisions):
        lines.append(f"  {prefix}: {', '.join(collisions[prefix])}")
    lines.append(
        "same-stem workbooks would silently overwrite each other's "
        "snapshots; rename one or give each entry a distinct snapshot_prefix"
    )
    raise ConfigError("\n".join(lines))


def run_estate(
    manifest: EstateManifest,
    phase: ParityPhase,
    *,
    ruleset: Ruleset,
    store: BaselineStore,
    legacy_session_factory: SessionFactory | None = None,
    target_session_factory: SessionFactory | None = None,
    post_swap: bool = False,
    profile_name: str | None = None,
    run_id: str | None = None,
    grain_top_n: int = 20,
) -> tuple[EstateResult, RunResult]:
    """Run one parity phase over every workbook in the manifest.

    phase "snapshot" sweeps the legacy session, "check" the target
    session, and "run" (both-live, D16) does one full snapshot sweep, then
    closes the legacy session, then one full check sweep - never two
    sessions open at once. A factory is called at most once per sweep and
    its session is reused for every workbook, then closed in a finally.
    All workbooks share one run_id so the warehouse audit trail groups the
    wave under a single QUERY_TAG (D12).

    Returns the assembled EstateResult plus the estate-level roll-up check
    run (M-ESTATE-* consume the result via extras[ESTATE_EXTRAS_KEY]).
    """
    if phase not in ("snapshot", "check", "run"):
        raise ValueError(f"unknown parity phase {phase!r}")
    if post_swap and phase != "check":
        # Mirrors runner.build_bundle: a snapshot is by definition taken
        # from the pre-swap legacy side.
        raise ValueError("--post-swap applies to the check phase only")
    run_id = run_id or str(uuid.uuid4())
    estate = EstateResult(
        phase=phase,
        entries=[
            WorkbookParity(workbook_path=entry.path, map_path=entry.map)
            for entry in manifest.workbooks
        ],
        manifest_ref=manifest.source_ref,
        created_at=utc_now().isoformat(),
    )

    if phase in ("snapshot", "run"):
        _sweep(
            manifest,
            estate,
            "snapshot",
            legacy_session_factory,
            ruleset=ruleset,
            store=store,
            run_id=run_id,
            profile_name=profile_name,
            grain_top_n=grain_top_n,
            post_swap=False,
        )
    if phase in ("check", "run"):
        _sweep(
            manifest,
            estate,
            "check",
            target_session_factory,
            ruleset=ruleset,
            store=store,
            run_id=run_id,
            profile_name=profile_name,
            grain_top_n=grain_top_n,
            post_swap=post_swap,
        )

    estate.rollup = estate.compute_rollup()
    ref = estate.manifest_ref
    name = (Path(ref).stem or "estate") if ref else "estate"
    rollup_run = run_checks(
        RunRequest(
            target=Target(type="parity", name=name, source_ref=ref),
            ruleset=ruleset,
            profile=profile_name,
            run_id=run_id,
            session=None,
            extras={ESTATE_EXTRAS_KEY: estate},
        )
    )
    return estate, rollup_run


def _sweep(
    manifest: EstateManifest,
    estate: EstateResult,
    mode: ParityMode,
    session_factory: SessionFactory | None,
    *,
    ruleset: Ruleset,
    store: BaselineStore,
    run_id: str,
    profile_name: str | None,
    grain_top_n: int,
    post_swap: bool,
) -> None:
    """One sequential pass over every entry with at most one session for
    the whole sweep (D12). Per-workbook failures land on the entry and
    never abort the sweep; the session is closed even when they do."""
    session = session_factory() if session_factory is not None else None
    try:
        for spec, entry in zip(manifest.workbooks, estate.entries, strict=True):
            if entry.error is not None:
                # Errored in an earlier sweep; the error already stands -
                # spend no queries on it.
                continue
            try:
                result = run_parity(
                    workbook=Path(entry.workbook_path),
                    mode=mode,
                    ruleset=ruleset,
                    store=store,
                    map_path=Path(entry.map_path) if entry.map_path else None,
                    session=session,
                    profile_name=profile_name,
                    run_id=run_id,
                    grain_top_n=grain_top_n,
                    post_swap=post_swap,
                    snapshot_prefix=spec.snapshot_prefix,
                )
            except (TableauParseError, ConfigError, ValueError) as exc:
                entry.error = str(exc)
                continue
            if mode == "snapshot":
                entry.snapshot_result = result
            else:
                entry.check_result = result
    finally:
        if session is not None:
            session.close()

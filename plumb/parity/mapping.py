"""Load and resolve the migration parity object map (map.yml).

The map is the explicit old->new contract for a migration (PARITY-PLAN D5).
Plumb's stance is report-don't-guess: a relation either matches a map entry
exactly, matches an ignore glob, falls through to identity (same FQN both
sides, when enabled), or lands in `unmapped` for M-MAP-001 to name. Fuzzy
inference never happens. A malformed map fails loudly with the file path
and reason — same policy as plumb/config. Refused relations (join/union/
extract-only) never enter the resolution lists; the M-* checks read them
straight off the ParityBundle.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from plumb.config.loader import ConfigError, load_yaml_mapping
from plumb.parity.contracts import MappingResolution, ResolvedObject, SourceRelation


def parse_fqn(fqn: str) -> tuple[str, str, str]:
    """Split a fully qualified DB.SCHEMA.TABLE name into its three parts.

    Raises ValueError on anything that is not exactly three non-empty
    parts; target (`new:`) objects must always be fully qualified."""
    parts = [part.strip() for part in fqn.split(".")]
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"expected a 3-part DB.SCHEMA.TABLE name, got {fqn!r}")
    return (parts[0], parts[1], parts[2])


def _name_parts(name: str) -> tuple[str, ...]:
    """Dot-split a relation/object name into upper-cased parts (Snowflake
    canonical form for comparison)."""
    return tuple(part.strip().upper() for part in name.split("."))


class ParityDefaults(BaseModel):
    """Map-wide defaults; per-object entries may override tolerance_pct.

    tolerance_pct is a FRACTION, not a percentage: 0.01 means 1% relative
    drift. Values above 1.0 (100%) are rejected loudly."""

    model_config = ConfigDict(extra="forbid")

    tolerance_pct: float = Field(default=0.01, ge=0.0, le=1.0)
    identity_fallback: bool = True


class ObjectMapping(BaseModel):
    """One explicit old->new object mapping.

    `old` is the name as the workbook references it: DB.SCHEMA.TABLE, or
    SCHEMA.TABLE when the connection supplies the database. `new` must be
    fully qualified. keys/grain/columns are stored upper-cased (Snowflake
    canonical). tolerance_pct is a FRACTION, not a percentage: 0.01 means
    1% relative drift; values above 1.0 (100%) are rejected loudly."""

    model_config = ConfigDict(extra="forbid")

    old: str = Field(min_length=1)
    new: str = Field(min_length=1)
    keys: list[str] = Field(default_factory=list)
    grain: list[str] = Field(default_factory=list)
    columns: dict[str, str] = Field(default_factory=dict)
    tolerance_pct: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("old")
    @classmethod
    def _old_is_qualified(cls, value: str) -> str:
        parts = [part.strip() for part in value.split(".")]
        if len(parts) not in (2, 3) or not all(parts):
            raise ValueError(
                f"old must be a 2- or 3-part SCHEMA.TABLE or DB.SCHEMA.TABLE name, got {value!r}"
            )
        return value

    @field_validator("new")
    @classmethod
    def _new_is_fully_qualified(cls, value: str) -> str:
        parse_fqn(value)
        return value

    @field_validator("keys", "grain")
    @classmethod
    def _column_names_upper(cls, value: list[str]) -> list[str]:
        cleaned = [name.strip().upper() for name in value]
        if not all(cleaned):
            raise ValueError("column names must be non-empty")
        return cleaned

    @field_validator("columns")
    @classmethod
    def _column_map_upper(cls, value: dict[str, str]) -> dict[str, str]:
        upper: dict[str, str] = {}
        seen_new: set[str] = set()
        for old_name, new_name in value.items():
            old_upper = str(old_name).strip().upper()
            new_upper = new_name.strip().upper()
            if not old_upper or not new_upper:
                raise ValueError("column rename names must be non-empty")
            if old_upper in upper:
                raise ValueError(f"duplicate column rename for {old_upper!r}")
            if new_upper in seen_new:
                raise ValueError(f"duplicate column rename target {new_upper!r}")
            seen_new.add(new_upper)
            upper[old_upper] = new_upper
        return upper


class ParityMap(BaseModel):
    """The validated content of a galaxy-map.yml file."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    defaults: ParityDefaults = Field(default_factory=ParityDefaults)
    objects: list[ObjectMapping] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)

    @field_validator("ignore")
    @classmethod
    def _ignore_patterns_non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [pattern.strip() for pattern in value]
        if not all(cleaned):
            raise ValueError("ignore patterns must be non-empty")
        return cleaned

    @model_validator(mode="after")
    def _old_names_unique(self) -> ParityMap:
        seen: set[str] = set()
        for entry in self.objects:
            key = entry.old.strip().upper()
            if key in seen:
                raise ValueError(f"duplicate old object in map: {entry.old!r}")
            seen.add(key)
        return self


def _format_validation_error(source: Path, exc: ValidationError) -> str:
    lines = [f"invalid parity map in {source}:"]
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "(root)"
        lines.append(f"  {location}: {err['msg']}")
    return "\n".join(lines)


def load_map(path: Path) -> ParityMap:
    """Load and validate a map.yml. Missing file, unparseable YAML, unknown
    keys, wrong types, a version other than 1, and duplicate `old` names all
    raise ConfigError with the file path and reason."""
    raw = load_yaml_mapping(path)
    try:
        return ParityMap.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc


def _matches(old: str, fqn: str) -> bool:
    """True when a map entry's `old` names the relation FQN.

    Comparison is case-insensitive and explicit, never fuzzy: when both
    sides carry the same number of parts they must all match; when one
    side omits the database (2-part), the trailing SCHEMA.TABLE parts
    must match the other side's tail. Tail matching needs at least two
    shared trailing parts — a bare 1-part name never tail-matches a
    multi-part entry (TABLE alone is too ambiguous to map safely)."""
    old_parts = _name_parts(old)
    fqn_parts = _name_parts(fqn)
    depth = min(len(old_parts), len(fqn_parts))
    if depth < 2:
        return old_parts == fqn_parts
    return old_parts[-depth:] == fqn_parts[-depth:]


def _match_entry(fqn: str, entries: list[ObjectMapping]) -> ObjectMapping | None:
    """First entry (file order) whose `old` matches the FQN, or None."""
    for entry in entries:
        if _matches(entry.old, fqn):
            return entry
    return None


def _is_ignored(fqn: str, patterns: list[str]) -> bool:
    """Case-insensitive fnmatch of the FQN against the ignore globs."""
    upper = fqn.upper()
    return any(fnmatchcase(upper, pattern.upper()) for pattern in patterns)


def resolve(relations: list[SourceRelation], parity_map: ParityMap) -> MappingResolution:
    """Resolve every parity-eligible relation against the map.

    Only table and custom_sql relations are resolvable; refused relations
    appear in none of the result lists. Custom SQL always resolves verbatim
    (the SQL runs as-is on both sides, PARITY-PLAN D6), so keys/grain/
    columns never apply to it. Table relations resolve in this order:
    explicit map entry, ignore glob, identity fallback (when enabled),
    otherwise `unmapped` for M-MAP-001 to report."""
    resolution = MappingResolution()
    defaults = parity_map.defaults
    for relation in relations:
        if relation.kind == "custom_sql":
            resolution.resolved.append(
                ResolvedObject(
                    relation=relation,
                    target_fqn="",
                    via_identity=True,
                    tolerance_pct=defaults.tolerance_pct,
                )
            )
            continue
        if relation.kind != "table":
            continue
        fqn = relation.fqn
        if fqn is None:
            resolution.unmapped.append(relation)
            continue
        entry = _match_entry(fqn, parity_map.objects)
        if entry is not None:
            tolerance = (
                entry.tolerance_pct
                if entry.tolerance_pct is not None
                else defaults.tolerance_pct
            )
            resolution.resolved.append(
                ResolvedObject(
                    relation=relation,
                    target_fqn=entry.new,
                    via_identity=False,
                    column_map=dict(entry.columns),
                    keys=tuple(entry.keys),
                    grain=tuple(entry.grain),
                    tolerance_pct=tolerance,
                )
            )
            continue
        if _is_ignored(fqn, parity_map.ignore):
            resolution.ignored.append(relation)
            continue
        # Identity requires a fully qualified (3-part) name: anything less
        # cannot name the same object on both sides, so it is unmapped for
        # M-MAP-001 to report rather than silently half-resolved.
        if defaults.identity_fallback and len(_name_parts(fqn)) >= 3:
            resolution.resolved.append(
                ResolvedObject(
                    relation=relation,
                    target_fqn=fqn,
                    via_identity=True,
                    tolerance_pct=defaults.tolerance_pct,
                )
            )
        else:
            resolution.unmapped.append(relation)
    return resolution


# --- post-swap mode (PARITY-PLAN-V2 E8, D14/D18) ----------------------------


def _new_name_collisions(parity_map: ParityMap) -> list[str]:
    """One message per `new` name claimed by more than one entry.

    Many-to-one maps are legal for forward checking (two legacy tables may
    both be proven against the same galaxy table — see the PARITY-PLAN-V2
    risk table); they only become an error when the map must be inverted,
    because the inverse direction is then ambiguous. That is why this lives
    here and not in a ParityMap validator."""
    by_new: dict[str, list[str]] = {}
    for entry in parity_map.objects:
        by_new.setdefault(entry.new.strip().upper(), []).append(entry.old)
    return [
        f"new object {new!r} is mapped from multiple old objects: {', '.join(olds)}"
        for new, olds in by_new.items()
        if len(olds) > 1
    ]


def invert_map(parity_map: ParityMap) -> ParityMap:
    """Swap old/new on every entry so the map reads new->old (D14).

    Side-ness, verified against metrics.measure(): an entry's keys/grain
    are authored in OLD-side (relation/legacy) names — measure() uses them
    as-is on side="legacy" and translates each through column_map only on
    side="target"; columns is {old: new}, and metrics are always REPORTED
    under the old names. Inverting therefore renames keys/grain into
    new-side terms via the entry's own columns rename (columns.get(name,
    name)) and flips columns to {new: old}, so a forward resolve with the
    inverted map hands measure() relation-side names it can use directly
    and a column_map pointing back at the legacy side. The flip is safe
    because column rename targets are validated unique at load time.

    `ignore` globs are authored against old-side names and CANNOT be
    mechanically translated — a glob over legacy names says nothing about
    which galaxy names to skip — so they are carried over unchanged;
    post-swap resolution matches them against the names in the artifact
    actually being checked.

    Raises ConfigError naming every offender when the map is not
    invertible: an `old` that is not fully qualified (a 2-part old cannot
    become a valid `new`, and cannot reconstruct the legacy snapshot FQN),
    or a duplicate `new` (non-injective: two legacy objects merged into one
    galaxy object — a map AUTHORING error per D14, refused at inversion
    time, never a runtime surprise).

    Property: for a fully-qualified injective map,
    invert_map(invert_map(m)) == m (entry order is preserved)."""
    problems = [
        f"old name {entry.old!r} is not fully qualified; a 2-part old cannot "
        "become a valid `new` and cannot reconstruct the legacy snapshot identity"
        for entry in parity_map.objects
        if len(_name_parts(entry.old)) != 3
    ]
    problems.extend(_new_name_collisions(parity_map))
    if problems:
        raise ConfigError(
            "cannot invert parity map for post-swap mode:\n  " + "\n  ".join(problems)
        )
    return ParityMap(
        version=1,
        defaults=parity_map.defaults.model_copy(),
        objects=[
            ObjectMapping(
                old=entry.new,
                new=entry.old,
                keys=[entry.columns.get(key, key) for key in entry.keys],
                grain=[entry.columns.get(name, name) for name in entry.grain],
                columns={new_col: old_col for old_col, new_col in entry.columns.items()},
                tolerance_pct=entry.tolerance_pct,
            )
            for entry in parity_map.objects
        ],
        ignore=list(parity_map.ignore),
    )


def _match_entry_by_new(fqn: str, entries: list[ObjectMapping]) -> ObjectMapping | None:
    """First entry (file order) whose `new` names the relation FQN, or None.

    Same explicit semantics as _matches: entry.new is always 3-part; the
    relation FQN may be 2-part when the swapped workbook omits the database
    (tail-match on >=2 shared trailing parts, case-insensitive, never
    1-part)."""
    for entry in entries:
        if _matches(entry.new, fqn):
            return entry
    return None


def resolve_post_swap(
    relations: list[SourceRelation], parity_map: ParityMap
) -> MappingResolution:
    """Resolve relations from the ALREADY-SWAPPED artifact (D14/D18).

    Table relations here carry NEW (galaxy) FQNs; the snapshots were taken
    under LEGACY names. Each relation is matched against entry.new (see
    _match_entry_by_new) and, when the entry's old is fully qualified, a
    synthetic legacy SourceRelation is built from it so snapshot_name()
    reproduces the pre-swap snapshot identity exactly; target_fqn stays the
    relation's own NEW FQN as the swapped workbook spells it, so
    measure(..., "target") measures the new object exactly as the v1 check
    phase would. This is deliberately NOT resolve(relations,
    invert_map(map)): a plain forward resolve keys the snapshot on the
    relation itself (the NEW name) and could never reach the legacy
    snapshots.

    A matched entry whose old is 2-part lands in resolution.uninvertible
    with a machine-readable reason, not in plain unmapped: without the
    database part the legacy snapshot FQN cannot be reconstructed, and
    M-MAP-001 must say so distinctly. Unmatched relations follow the v1
    order: ignore glob (patterns are matched against the relation's own FQN
    — the names in the artifact being checked, here the NEW names), then
    identity fallback (3-part only, same name both sides), else unmapped.
    Refused relations appear in no list. Custom SQL resolves verbatim
    exactly as v1 — but custom SQL whose text was edited during the swap
    hashes to a different snapshot label and surfaces via M-SNAP-001.

    Case caveat: snapshot_name()'s trailing hash is case-SENSITIVE over the
    datasource|label text, so entry.old must be spelled EXACTLY as the
    pre-swap workbook spelled the FQN (case and database qualification
    included). A mismatch surfaces honestly as a missing snapshot
    (M-SNAP-001); the remediation is to re-spell `old:` in the map to match
    the pre-swap workbook's spelling, not to re-snapshot.

    Raises ConfigError before resolving anything when the map's `new` names
    are not injective, naming every collision (D14: a map authoring error,
    not a runtime surprise)."""
    collisions = _new_name_collisions(parity_map)
    if collisions:
        raise ConfigError(
            "cannot resolve post-swap: the map is not injective, so new names "
            "cannot identify their legacy objects:\n  " + "\n  ".join(collisions)
        )
    resolution = MappingResolution()
    defaults = parity_map.defaults
    for relation in relations:
        if relation.kind == "custom_sql":
            resolution.resolved.append(
                ResolvedObject(
                    relation=relation,
                    target_fqn="",
                    via_identity=True,
                    tolerance_pct=defaults.tolerance_pct,
                )
            )
            continue
        if relation.kind != "table":
            continue
        fqn = relation.fqn
        if fqn is None:
            resolution.unmapped.append(relation)
            continue
        entry = _match_entry_by_new(fqn, parity_map.objects)
        if entry is not None:
            if len(_name_parts(entry.old)) != 3:
                resolution.uninvertible.append(
                    (
                        relation,
                        f"old name {entry.old} is not fully qualified; cannot "
                        "reconstruct the legacy snapshot identity",
                    )
                )
                continue
            database, schema, table = parse_fqn(entry.old)
            synthetic = SourceRelation(
                datasource=relation.datasource,
                kind="table",
                database=database,
                schema=schema,
                table=table,
                connection_class=relation.connection_class,
                has_extract=relation.has_extract,
            )
            tolerance = (
                entry.tolerance_pct
                if entry.tolerance_pct is not None
                else defaults.tolerance_pct
            )
            resolution.resolved.append(
                ResolvedObject(
                    relation=synthetic,
                    target_fqn=fqn,
                    via_identity=False,
                    column_map=dict(entry.columns),
                    keys=tuple(entry.keys),
                    grain=tuple(entry.grain),
                    tolerance_pct=tolerance,
                )
            )
            continue
        if _is_ignored(fqn, parity_map.ignore):
            resolution.ignored.append(relation)
            continue
        if defaults.identity_fallback and len(_name_parts(fqn)) >= 3:
            resolution.resolved.append(
                ResolvedObject(
                    relation=relation,
                    target_fqn=fqn,
                    via_identity=True,
                    tolerance_pct=defaults.tolerance_pct,
                )
            )
        else:
            resolution.unmapped.append(relation)
    return resolution

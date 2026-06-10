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

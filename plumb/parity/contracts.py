"""Shared data contracts for the migration parity family.

Every parity module (sources, mapping, metrics, runner) and the M-* checks
speak these types. They are owned here so the modules stay decoupled:
sources.py produces SourceRelation, mapping.py resolves them, metrics.py
produces ParityMetrics, the runner assembles a ParityBundle, and the checks
consume the bundle as pure comparisons. Changing a field here is a contract
change for the whole family; treat it like engine/models.py.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

RelationKind = Literal["table", "custom_sql", "refused"]

# Machine-readable refusal reasons (PARITY-PLAN D6). "extract-only" means the
# datasource has no live relational connection at all (.hyper only); a
# datasource that has an extract *over* a live Snowflake relation is eligible,
# because parity is proven against the warehouse objects the extract refreshes
# from, not against the extract bytes.
REFUSAL_JOIN = "join"
REFUSAL_UNION = "union"
REFUSAL_EXTRACT_ONLY = "extract-only"
REFUSAL_UNRECOGNIZED = "unrecognized"


@dataclass(frozen=True)
class SourceRelation:
    """One physical source a workbook datasource depends on."""

    datasource: str
    kind: RelationKind
    database: str | None = None
    schema: str | None = None
    table: str | None = None
    custom_sql: str | None = None
    connection_class: str | None = None
    refusal_reason: str | None = None
    has_extract: bool = False

    @property
    def fqn(self) -> str | None:
        """DB.SCHEMA.TABLE as referenced by the workbook, or None for
        custom SQL / refused relations."""
        if self.kind != "table" or not self.table:
            return None
        parts = [p for p in (self.database, self.schema, self.table) if p]
        return ".".join(parts)

    @property
    def label(self) -> str:
        """A stable human/snapshot identifier for this relation."""
        if self.kind == "table":
            return self.fqn or self.table or "unknown-table"
        if self.kind == "custom_sql":
            return f"custom-sql-{_short_hash(self.custom_sql or '')}"
        return f"refused-{self.refusal_reason or 'unknown'}"


@dataclass(frozen=True)
class ResolvedObject:
    """A parity-eligible relation resolved to its target-side object."""

    relation: SourceRelation
    target_fqn: str
    via_identity: bool = False
    column_map: dict[str, str] = field(default_factory=dict)
    keys: tuple[str, ...] = ()
    grain: tuple[str, ...] = ()
    tolerance_pct: float = 0.01


@dataclass
class MappingResolution:
    """The outcome of resolving every eligible relation against map.yml."""

    resolved: list[ResolvedObject] = field(default_factory=list)
    unmapped: list[SourceRelation] = field(default_factory=list)
    ignored: list[SourceRelation] = field(default_factory=list)


@dataclass
class ColumnMetrics:
    data_type: str
    null_count: int
    sum_value: float | None = None
    min_value: float | None = None
    max_value: float | None = None


@dataclass
class GrainGroup:
    group: dict[str, str]
    count: int


@dataclass
class ParityMetrics:
    """The complete parity measurement of one object on one side."""

    object_fqn: str
    row_count: int
    columns: dict[str, ColumnMetrics] = field(default_factory=dict)
    distinct_counts: dict[str, int] = field(default_factory=dict)
    grain_groups: list[GrainGroup] = field(default_factory=list)

    def to_records(self) -> list[dict[str, Any]]:
        """Flat records for the parquet baseline store. Schema is stable:
        kind / column / value / text. The first record is the codec version
        marker; from_records refuses record sets without it."""
        records: list[dict[str, Any]] = [
            {"kind": "codec", "column": None, "value": CODEC_VERSION, "text": None},
            {"kind": "object_fqn", "column": None, "value": None, "text": self.object_fqn},
            {"kind": "row_count", "column": None, "value": float(self.row_count), "text": None},
        ]
        for name in sorted(self.columns):
            col = self.columns[name]
            records.append(
                {"kind": "data_type", "column": name, "value": None, "text": col.data_type}
            )
            records.append(
                {
                    "kind": "null_count",
                    "column": name,
                    "value": float(col.null_count),
                    "text": None,
                }
            )
            for kind, value in (
                ("sum", col.sum_value),
                ("min", col.min_value),
                ("max", col.max_value),
            ):
                if value is not None:
                    records.append(
                        {"kind": kind, "column": name, "value": float(value), "text": None}
                    )
        for key in sorted(self.distinct_counts):
            records.append(
                {
                    "kind": "distinct",
                    "column": key,
                    "value": float(self.distinct_counts[key]),
                    "text": None,
                }
            )
        for grp in self.grain_groups:
            records.append(
                {
                    "kind": "grain",
                    "column": None,
                    "value": float(grp.count),
                    "text": json.dumps(grp.group, sort_keys=True),
                }
            )
        return records

    @classmethod
    def from_records(cls, records: list[dict[str, Any]]) -> ParityMetrics:
        """Rebuild metrics from stored records. Raises ValueError when the
        codec marker is absent or names a version this build cannot read,
        so a stale or foreign snapshot fails loudly instead of decoding to
        silently-wrong metrics."""
        codec = next((r.get("value") for r in records if r.get("kind") == "codec"), None)
        if codec != CODEC_VERSION:
            raise ValueError("unsupported or missing parity snapshot codec")
        metrics = cls(object_fqn="", row_count=0)
        for rec in records:
            kind = rec.get("kind")
            column = rec.get("column")
            value = rec.get("value")
            text = rec.get("text")
            if kind == "object_fqn":
                metrics.object_fqn = str(text or "")
            elif kind == "row_count":
                metrics.row_count = int(value or 0)
            elif kind == "data_type" and column:
                metrics.columns.setdefault(
                    str(column), ColumnMetrics(data_type="", null_count=0)
                ).data_type = str(text or "")
            elif kind == "null_count" and column:
                metrics.columns.setdefault(
                    str(column), ColumnMetrics(data_type="", null_count=0)
                ).null_count = int(value or 0)
            elif kind in ("sum", "min", "max") and column:
                col = metrics.columns.setdefault(
                    str(column), ColumnMetrics(data_type="", null_count=0)
                )
                if kind == "sum":
                    col.sum_value = float(value) if value is not None else None
                elif kind == "min":
                    col.min_value = float(value) if value is not None else None
                else:
                    col.max_value = float(value) if value is not None else None
            elif kind == "distinct" and column:
                metrics.distinct_counts[str(column)] = int(value or 0)
            elif kind == "grain":
                group = json.loads(str(text or "{}"))
                metrics.grain_groups.append(GrainGroup(group=group, count=int(value or 0)))
        return metrics


RECORD_COLUMNS = ["kind", "column", "value", "text"]

# Snapshot record codec version. Bump on any incompatible record-shape
# change; from_records refuses snapshots written by other versions.
CODEC_VERSION = 1.0

ParityMode = Literal["snapshot", "check"]
ParitySide = Literal["legacy", "target"]


@dataclass
class ParityBundle:
    """Everything the M-* checks need, assembled by parity/runner.py and
    carried in CheckContext.extras["parity_bundle"]. The checks are pure
    comparisons over this bundle: they run no SQL themselves, so a check-
    phase run executes each metric query exactly once."""

    mode: ParityMode
    workbook_path: str
    relations: list[SourceRelation] = field(default_factory=list)
    resolution: MappingResolution | None = None
    snapshot_prefix: str = ""
    side: ParitySide = "legacy"
    # keyed by snapshot_name(...) for the relation
    live_metrics: dict[str, ParityMetrics] = field(default_factory=dict)
    snapshots: dict[str, ParityMetrics] = field(default_factory=dict)
    # per-snapshot-name errors during discovery/measurement -> check ERROR
    errors: dict[str, str] = field(default_factory=dict)
    # set when no session was available (static-only) -> value checks SKIP
    live_unavailable_reason: str | None = None


EXTRAS_KEY = "parity_bundle"

_SAFE_PART = re.compile(r"[^a-z0-9_-]+")


def _sanitize(part: str) -> str:
    cleaned = _SAFE_PART.sub("-", part.lower()).strip("-")
    return cleaned or "x"


def _short_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def snapshot_prefix_for(workbook_path: str) -> str:
    from pathlib import Path

    return f"parity__{_sanitize(Path(workbook_path).stem)}"


def snapshot_name(prefix: str, relation: SourceRelation) -> str:
    """Flat, filesystem-safe baseline name for one relation's snapshot.

    The trailing 6-char hash is derived from the unsanitized datasource and
    label, so two relations whose names sanitize to the same text still get
    distinct snapshot names (a collision would silently overwrite)."""
    hash6 = _short_hash(f"{relation.datasource}|{relation.label}")[:6]
    return f"{prefix}__{_sanitize(relation.datasource)}__{_sanitize(relation.label)}__{hash6}"

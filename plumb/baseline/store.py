"""Baseline store: save and load golden result sets.

The default is local Parquet (row data) plus a JSON manifest (schema,
fingerprints, provenance), under ~/.plumb/baselines. The BaselineStore
Protocol is the seam: Phase 2 can add a shared Snowflake stage or object
store implementation with no change to the regression check, which only
talks to the interface.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import pyarrow as pa
import pyarrow.parquet as pq

from plumb.engine.models import utc_now

BASELINE_HOME = Path.home() / ".plumb" / "baselines"


@dataclass
class Baseline:
    name: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    aggregates: dict[str, float] = field(default_factory=dict)
    created_at: str = ""
    source_ref: str | None = None
    ruleset_version: str | None = None

    def manifest(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("rows")
        return data


@runtime_checkable
class BaselineStore(Protocol):
    def exists(self, name: str) -> bool: ...
    def save(self, baseline: Baseline) -> None: ...
    def load(self, name: str) -> Baseline | None: ...
    def list_names(self) -> list[str]: ...


def compute_aggregates(columns: list[str], rows: list[dict[str, Any]]) -> dict[str, float]:
    """A cheap numeric fingerprint: row count plus the sum and non-null
    count of every column whose values parse as numbers. Used by R-AGG-001
    and as a fast signal in R-DIFF-001."""
    aggregates: dict[str, float] = {"__row_count": float(len(rows))}
    for col in columns:
        total = 0.0
        non_null = 0
        numeric = True
        for row in rows:
            value = row.get(col)
            if value is None:
                continue
            try:
                total += float(value)
                non_null += 1
            except (TypeError, ValueError):
                numeric = False
                break
        if numeric:
            aggregates[f"sum:{col}"] = total
            aggregates[f"count:{col}"] = float(non_null)
    return aggregates


def make_baseline(
    name: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    source_ref: str | None = None,
    ruleset_version: str | None = None,
) -> Baseline:
    return Baseline(
        name=name,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        aggregates=compute_aggregates(columns, rows),
        created_at=utc_now().isoformat(),
        source_ref=source_ref,
        ruleset_version=ruleset_version,
    )


class LocalParquetStore:
    """Local Parquet plus manifest implementation of BaselineStore."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or BASELINE_HOME

    def _parquet_path(self, name: str) -> Path:
        return self.root / f"{name}.parquet"

    def _manifest_path(self, name: str) -> Path:
        return self.root / f"{name}.manifest.json"

    def exists(self, name: str) -> bool:
        return self._manifest_path(name).exists() and self._parquet_path(name).exists()

    def save(self, baseline: Baseline) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if baseline.columns:
            table = pa.Table.from_pylist(
                [{c: row.get(c) for c in baseline.columns} for row in baseline.rows]
            )
        else:
            table = pa.table({})
        pq.write_table(table, self._parquet_path(baseline.name))
        self._manifest_path(baseline.name).write_text(
            json.dumps(baseline.manifest(), indent=2, default=str), encoding="utf-8"
        )

    def load(self, name: str) -> Baseline | None:
        if not self.exists(name):
            return None
        manifest = json.loads(self._manifest_path(name).read_text(encoding="utf-8"))
        table = pq.read_table(self._parquet_path(name))
        rows = table.to_pylist()
        return Baseline(
            name=manifest["name"],
            columns=manifest["columns"],
            rows=rows,
            row_count=manifest["row_count"],
            aggregates=manifest.get("aggregates", {}),
            created_at=manifest.get("created_at", ""),
            source_ref=manifest.get("source_ref"),
            ruleset_version=manifest.get("ruleset_version"),
        )

    def list_names(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.stem for p in self.root.glob("*.parquet"))

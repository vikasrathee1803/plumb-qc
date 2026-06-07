"""Baseline store: local and shared, behind one Protocol. The shared store
is the Phase 2 acceptance: a teammate's machine reproduces the same diff."""

from collections import Counter
from pathlib import Path

from plumb.baseline.store import (
    BaselineStore,
    LocalParquetStore,
    SharedFileStore,
    make_baseline,
    make_baseline_store,
)
from plumb.checks.sql_regression import _row_key
from plumb.config.models import BaselineStoreConfig

ROWS = [
    {"REGION": "EAST", "AMOUNT": 100},
    {"REGION": "WEST", "AMOUNT": 200},
]


def test_local_roundtrip(tmp_path: Path):
    store = LocalParquetStore(tmp_path)
    store.save(make_baseline("b", ["REGION", "AMOUNT"], ROWS, ruleset_version="1"))
    loaded = store.load("b")
    assert loaded is not None
    assert loaded.row_count == 2
    assert loaded.rows == ROWS
    assert "b" in store.list_names()


def test_both_stores_satisfy_the_protocol(tmp_path: Path):
    assert isinstance(LocalParquetStore(tmp_path), BaselineStore)
    assert isinstance(SharedFileStore(tmp_path), BaselineStore)


def test_factory_selects_kind(tmp_path: Path):
    assert isinstance(make_baseline_store("local", tmp_path), LocalParquetStore)
    assert isinstance(make_baseline_store("shared", tmp_path), SharedFileStore)


def test_shared_store_writes_an_index(tmp_path: Path):
    store = SharedFileStore(tmp_path)
    store.save(make_baseline("sales", ["REGION", "AMOUNT"], ROWS, ruleset_version="2026.06.0"))
    index = store.index()
    assert "sales" in index
    assert index["sales"]["row_count"] == 2
    assert index["sales"]["ruleset_version"] == "2026.06.0"


def test_teammate_reproduces_the_same_diff(tmp_path: Path):
    """Author saves a baseline to the shared location; a teammate opens a
    fresh store at the same path, loads it, and computes the identical diff
    against changed output."""
    shared = tmp_path / "team"
    author = SharedFileStore(shared)
    author.save(make_baseline("sales_daily", ["REGION", "AMOUNT"], ROWS))

    teammate = SharedFileStore(shared)  # a different machine, same path
    loaded = teammate.load("sales_daily")
    assert loaded is not None

    changed = [{"REGION": "EAST", "AMOUNT": 100}, {"REGION": "WEST", "AMOUNT": 999}]
    base_counter = Counter(_row_key(r) for r in loaded.rows)
    cur_counter = Counter(_row_key(r) for r in changed)
    added = sum((cur_counter - base_counter).values())
    removed = sum((base_counter - cur_counter).values())
    assert (added, removed) == (1, 1)


def test_shared_config_requires_path():
    import pytest

    with pytest.raises(Exception, match="shared baseline store requires a path"):
        BaselineStoreConfig(kind="shared")

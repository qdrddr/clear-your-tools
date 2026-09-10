"""Unit tests for tool examples SQLite store."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from cyt.tool_examples.hash_utils import content_hash
from cyt.tool_examples.store import ToolExamplesStore


@pytest.fixture
def store(tmp_path: Path) -> ToolExamplesStore:
    db = tmp_path / "tool_examples.db"
    return ToolExamplesStore.open(str(db))


def test_project_registry_dedupes_by_root(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    pid1 = store.get_or_create_project(str(root))
    pid2 = store.get_or_create_project(str(root))
    assert pid1 == pid2


def test_upsert_capture_dedupes_identical_calls(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    args = {"query": "bm25"}
    sid1 = store.upsert_capture(project_id, "codebase-memory-mcp", "search_graph", schema, args)
    sid2 = store.upsert_capture(project_id, "codebase-memory-mcp", "search_graph", schema, args)
    assert sid1 == sid2
    captures = store.list_captures(project_id, "codebase-memory-mcp", "search_graph")
    assert len(captures) == 1


def test_different_args_create_separate_captures(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    sid1 = store.upsert_capture(project_id, "srv", "tool", schema, {"query": "alpha"})
    sid2 = store.upsert_capture(project_id, "srv", "tool", schema, {"query": "beta"})
    assert sid1 != sid2
    assert len(store.list_captures(project_id, "srv", "tool")) == 2


def test_example_rows_linked_to_capture(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"project": {"type": "string"}}}
    args = {"project": "clear-your-tools"}
    schema_id = store.upsert_capture(project_id, "codebase-memory-mcp", "search_graph", schema, args)
    rows = store.list_examples_for_path(
        [schema_id],
        "inputSchema.properties.project",
    )
    assert len(rows) == 1
    assert rows[0].value == '"clear-your-tools"'


def test_get_capture_round_trip(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"limit": {"type": "integer"}}}
    args = {"limit": 5}
    schema_id = store.upsert_capture(project_id, "srv", "tool", schema, args)
    capture = store.get_capture(schema_id)
    assert capture is not None
    assert capture.schema_json == schema
    assert capture.input_json == args
    assert capture.schema_hash == content_hash(schema)
    assert capture.input_hash == content_hash(args)


def test_list_captures_filters_by_schema_hash(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema_v1 = {"type": "object", "properties": {"query": {"type": "string"}}}
    schema_v2 = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    }
    store.upsert_capture(project_id, "srv", "tool", schema_v1, {"query": "a"})
    store.upsert_capture(project_id, "srv", "tool", schema_v2, {"query": "b", "limit": 10})
    filtered = store.list_captures(
        project_id,
        "srv",
        "tool",
        schema_hash=content_hash(schema_v2),
    )
    assert len(filtered) == 1
    assert filtered[0].input_json == {"query": "b", "limit": 10}


def test_enforce_capture_retention(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    for idx in range(8):
        store.upsert_capture(project_id, "srv", "tool", schema, {"n": idx})
    store.enforce_capture_retention(max_captures=5, min_captures=3)
    captures = store.list_captures(project_id, "srv", "tool", limit=20)
    assert len(captures) == 5


def test_prune_stale_examples_respects_min_per_path(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    schema_id = store.upsert_capture(project_id, "srv", "tool", schema, {"query": "old"})
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - 365 * 86400 * 1000
    with store._lock:  # noqa: SLF001
        store._conn.execute(
            "UPDATE tool_example SET timestamp_ms = ? WHERE schema_id = ?",
            (old_ms, schema_id),
        )
        store._conn.commit()
    store.prune_stale_examples(cutoff_ms=now_ms - 86400000, min_per_path=3)
    rows = store.list_examples_for_path([schema_id], "inputSchema.properties.query")
    assert len(rows) == 1


def test_cleanup_orphan_example_paths(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    args = {"query": "valid"}
    schema_id = store.upsert_capture(project_id, "srv", "tool", schema, args)
    now_ms = int(time.time() * 1000)
    with store._lock:  # noqa: SLF001
        store._conn.execute(
            "INSERT INTO tool_example(schema_id, json_path, value, value_type, timestamp_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (schema_id, "inputSchema.properties.removed", '"orphan"', "string", now_ms),
        )
        store._conn.commit()
    store.cleanup_orphan_example_paths()
    rows = store.list_examples_for_path([schema_id], "inputSchema.properties.removed")
    assert rows == []


def test_record_examples_upserts_rows(store: ToolExamplesStore, tmp_path: Path) -> None:
    from cyt.tool_examples.flatten import FlattenedExample

    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    schema_id = store.upsert_capture(project_id, "srv", "tool", schema, {"q": "first"})
    store.record_examples(
        schema_id,
        [FlattenedExample(json_path="inputSchema.properties.q", value='"second"', value_type="string")],
    )
    rows = store.list_examples_for_path([schema_id], "inputSchema.properties.q", limit=10)
    values = {row.value for row in rows}
    assert '"first"' in values
    assert '"second"' in values


def test_enforce_example_path_limit(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    schema_id = store.upsert_capture(project_id, "srv", "tool", schema, {"query": "seed"})
    now_ms = int(time.time() * 1000)
    with store._lock:  # noqa: SLF001
        for idx in range(6):
            store._conn.execute(
                "INSERT INTO tool_example(schema_id, json_path, value, value_type, timestamp_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (schema_id, "inputSchema.properties.query", f'"v{idx}"', "string", now_ms + idx),
            )
        store._conn.commit()
    store.enforce_example_path_limit(max_per_path=3, min_per_path=2)
    rows = store.list_examples_for_path([schema_id], "inputSchema.properties.query", limit=10)
    assert len(rows) == 3


def test_cascade_delete_on_capture_removal(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    schema_id = store.upsert_capture(project_id, "srv", "tool", schema, {"x": "a"})
    with store._lock:  # noqa: SLF001
        store._conn.execute("DELETE FROM tool_input_schema WHERE schema_id = ?", (schema_id,))
        store._conn.commit()
    with store._lock:  # noqa: SLF001
        count = store._conn.execute("SELECT COUNT(*) FROM tool_example").fetchone()[0]
    assert int(count) == 0

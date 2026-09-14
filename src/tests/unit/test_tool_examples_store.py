"""Unit tests for tool examples SQLite store."""

from __future__ import annotations

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


def test_schema_v2_migration_adds_success_count(tmp_path: Path) -> None:
    db = tmp_path / "legacy_tool_examples.db"
    conn = __import__("sqlite3").connect(str(db))
    conn.executescript(
        """
        CREATE TABLE tool_example_project (
            project_id INTEGER PRIMARY KEY AUTOINCREMENT,
            root_path TEXT NOT NULL UNIQUE,
            created_ms INTEGER NOT NULL,
            last_seen_ms INTEGER NOT NULL
        );
        CREATE TABLE tool_input_schema (
            schema_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            mcp_server TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            schema_json TEXT NOT NULL,
            input_json TEXT NOT NULL,
            schema_hash TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            first_seen_ms INTEGER NOT NULL,
            last_seen_ms INTEGER NOT NULL
        );
        CREATE TABLE tool_example (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_id INTEGER NOT NULL,
            json_path TEXT NOT NULL,
            value TEXT NOT NULL,
            value_type TEXT NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            UNIQUE(schema_id, json_path, value)
        );
        PRAGMA user_version = 1;
        """,
    )
    conn.execute(
        "INSERT INTO tool_example_project(root_path, created_ms, last_seen_ms) VALUES (?, ?, ?)",
        (str(tmp_path / "repo"), 1, 1),
    )
    conn.execute(
        "INSERT INTO tool_input_schema("
        "project_id, mcp_server, tool_name, schema_json, input_json, "
        "schema_hash, input_hash, first_seen_ms, last_seen_ms"
        ") VALUES (1, 'srv', 'tool', '{}', '{}', 'h1', 'h2', 1, 1)",
    )
    conn.execute(
        "INSERT INTO tool_example(schema_id, json_path, value, value_type, timestamp_ms) "
        "VALUES (1, 'inputSchema.properties.q', '\"legacy\"', 'string', 1)",
    )
    conn.commit()
    conn.close()

    store = ToolExamplesStore.open(str(db))
    try:
        rows = store.list_examples_for_path([1], "inputSchema.properties.q")
        assert len(rows) == 1
        assert rows[0].success_count == 1
        with store._lock:
            version = int(store._conn.execute("PRAGMA user_version").fetchone()[0])
        assert version == 2
    finally:
        store.close()


def test_success_count_increments_on_repeat_capture(store: ToolExamplesStore, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    args = {"query": "bm25"}
    schema_id = store.upsert_capture(project_id, "srv", "tool", schema, args)
    store.upsert_capture(project_id, "srv", "tool", schema, args)
    rows = store.list_examples_for_path([schema_id], "inputSchema.properties.query")
    assert len(rows) == 1
    assert rows[0].success_count == 2


def test_list_aggregated_examples_for_path_sums_success_count(
    store: ToolExamplesStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"project": {"type": "string"}}}
    sid1 = store.upsert_capture(
        project_id,
        "srv",
        "tool",
        schema,
        {"project": "clear-your-tools"},
    )
    sid2 = store.upsert_capture(
        project_id,
        "srv",
        "tool",
        schema,
        {"project": "clear-your-tools"},
    )
    rows = store.list_aggregated_examples_for_path(
        [sid1, sid2],
        "inputSchema.properties.project",
    )
    assert len(rows) == 1
    assert rows[0].value == '"clear-your-tools"'
    assert rows[0].success_count == 2


def test_enforce_example_path_limit_keeps_high_usage_values(
    store: ToolExamplesStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    schema_id = store.upsert_capture(project_id, "srv", "tool", schema, {"query": "seed"})
    now_ms = int(time.time() * 1000)
    with store._lock:
        for idx in range(5):
            store._conn.execute(
                "INSERT INTO tool_example("
                "schema_id, json_path, value, value_type, timestamp_ms, success_count"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    schema_id,
                    "inputSchema.properties.query",
                    f'"v{idx}"',
                    "string",
                    now_ms + idx,
                    idx + 1,
                ),
            )
        store._conn.commit()
    store.enforce_example_path_limit(max_per_path=3, min_per_path=2)
    rows = store.list_examples_for_path([schema_id], "inputSchema.properties.query", limit=10)
    values = {row.value for row in rows}
    assert len(rows) == 3
    assert '"v0"' not in values
    assert '"v1"' not in values


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
    schema_id = store.upsert_capture(
        project_id,
        "codebase-memory-mcp",
        "search_graph",
        schema,
        args,
    )
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


def test_prune_stale_examples_respects_min_per_path(
    store: ToolExamplesStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project_id = store.get_or_create_project(str(root))
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    schema_id = store.upsert_capture(project_id, "srv", "tool", schema, {"query": "old"})
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - 365 * 86400 * 1000
    with store._lock:
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
    with store._lock:
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
        [
            FlattenedExample(
                json_path="inputSchema.properties.q",
                value='"second"',
                value_type="string",
            ),
        ],
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
    with store._lock:
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
    with store._lock:
        store._conn.execute("DELETE FROM tool_input_schema WHERE schema_id = ?", (schema_id,))
        store._conn.commit()
    with store._lock:
        count = store._conn.execute("SELECT COUNT(*) FROM tool_example").fetchone()[0]
    assert int(count) == 0

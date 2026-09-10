"""Unit tests for tool examples maintenance jobs."""

from __future__ import annotations

import time
from pathlib import Path

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tool_examples.maintenance import (
    run_tool_examples_maintenance,
    schedule_tool_examples_maintenance,
    valid_paths_for_capture,
)
from cyt.tool_examples.store import ToolExamplesStore


def test_valid_paths_for_capture_merges_schema_and_args() -> None:
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            },
        },
    }
    args = {"query": "bm25", "items": [{"name": "bar"}]}
    paths = valid_paths_for_capture(schema, args)
    assert "inputSchema.properties.query" in paths
    assert "inputSchema.properties.items.items[].properties.name" in paths
    assert "inputSchema.properties.items" in paths


def test_run_tool_examples_maintenance_prunes_old_examples(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    db = tmp_path / "tool_examples.db"
    config = set_hook_workspace_in_config(
        {
            "tools": {
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db)},
                    "retention": {
                        "max_per_path": 20,
                        "min_per_path": 1,
                        "max_captures_per_tool": 50,
                        "min_captures_per_tool": 1,
                        "max_age_days": 1,
                    },
                },
            },
        },
        root,
    )
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(root))
        schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        schema_id = store.upsert_capture(project_id, "srv", "tool", schema, {"query": "old"})
        now_ms = int(time.time() * 1000)
        stale_ms = now_ms - 3 * 86400 * 1000
        with store._lock:
            for _ in range(4):
                store._conn.execute(
                    "INSERT INTO tool_example(schema_id, json_path, value, value_type, timestamp_ms) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(schema_id, json_path, value) DO UPDATE SET timestamp_ms = excluded.timestamp_ms",
                    (schema_id, "inputSchema.properties.query", f'"v{_}"', "string", stale_ms),
                )
            store._conn.commit()
    finally:
        store.close()

    run_tool_examples_maintenance(config)

    store = ToolExamplesStore.open(str(db))
    try:
        rows = store.list_examples_for_path([schema_id], "inputSchema.properties.query", limit=20)
        assert len(rows) <= 1
    finally:
        store.close()


def test_schedule_tool_examples_maintenance_noop_when_disabled(tmp_path: Path) -> None:
    config = {"tools": {"examples": {"enabled": False}}}
    schedule_tool_examples_maintenance(config)


def test_run_maintenance_enforces_path_limit(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    db = tmp_path / "tool_examples.db"
    config = set_hook_workspace_in_config(
        {
            "tools": {
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db)},
                    "retention": {
                        "max_per_path": 2,
                        "min_per_path": 1,
                        "max_captures_per_tool": 50,
                        "min_captures_per_tool": 1,
                        "max_age_days": 365,
                    },
                },
            },
        },
        root,
    )
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(root))
        schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        schema_id = store.upsert_capture(project_id, "srv", "tool", schema, {"query": "v0"})
        import time

        now_ms = int(time.time() * 1000)
        with store._lock:
            for idx in range(1, 5):
                store._conn.execute(
                    "INSERT INTO tool_example(schema_id, json_path, value, value_type, timestamp_ms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        schema_id,
                        "inputSchema.properties.query",
                        f'"v{idx}"',
                        "string",
                        now_ms + idx,
                    ),
                )
            store._conn.commit()
    finally:
        store.close()

    run_tool_examples_maintenance(config)

    store = ToolExamplesStore.open(str(db))
    try:
        rows = store.list_examples_for_path([schema_id], "inputSchema.properties.query", limit=10)
        assert len(rows) == 2
    finally:
        store.close()

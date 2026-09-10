"""Unit tests for tool_input_schema capture versioning and retrieval."""

from __future__ import annotations

from pathlib import Path

from cyt.tool_examples.hash_utils import content_hash
from cyt.tool_examples.record import record_tool_examples_capture
from cyt.tool_examples.store import ToolExamplesStore


def _examples_config(db_path: Path, workspace: Path) -> dict:
    from cyt.hook.workspace_config import set_hook_workspace_in_config

    base = {
        "tools": {
            "examples": {
                "enabled": True,
                "database": {"path": str(db_path)},
            },
        },
    }
    return set_hook_workspace_in_config(base, workspace)


def test_schema_hash_changes_with_schema_structure(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    db = tmp_path / "tool_examples.db"
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(root))
        schema_v1 = {"type": "object", "properties": {"query": {"type": "string"}}}
        schema_v2 = {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        }
        sid_v1 = store.upsert_capture(project_id, "srv", "search", schema_v1, {"query": "x"})
        sid_v2 = store.upsert_capture(project_id, "srv", "search", schema_v2, {"query": "x", "limit": 5})
        assert sid_v1 != sid_v2
        cap_v1 = store.get_capture(sid_v1)
        cap_v2 = store.get_capture(sid_v2)
        assert cap_v1 is not None and cap_v2 is not None
        assert cap_v1.schema_hash != cap_v2.schema_hash
        assert cap_v1.schema_hash == content_hash(schema_v1)
        assert cap_v2.schema_hash == content_hash(schema_v2)
    finally:
        store.close()


def test_record_tool_examples_capture_uses_git_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    db = tmp_path / "tool_examples.db"
    config = _examples_config(db, nested)
    schema = {"type": "object", "properties": {"project": {"type": "string"}}}
    args = {"project": "demo"}
    schema_id = record_tool_examples_capture(
        workspace=nested,
        mcp_server="codebase-memory-mcp",
        tool_name="search_graph",
        input_schema=schema,
        args=args,
        config=config,
    )
    assert schema_id is not None
    store = ToolExamplesStore.open(str(db))
    try:
        capture = store.get_capture(schema_id)
        assert capture is not None
        assert capture.input_json == args
        assert capture.mcp_server == "codebase-memory-mcp"
        assert capture.tool_name == "search_graph"
    finally:
        store.close()


def test_list_captures_orders_by_recency(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    db = tmp_path / "tool_examples.db"
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(root))
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        first = store.upsert_capture(project_id, "srv", "tool", schema, {"n": 1})
        second = store.upsert_capture(project_id, "srv", "tool", schema, {"n": 2})
        store.upsert_capture(project_id, "srv", "tool", schema, {"n": 1})
        captures = store.list_captures(project_id, "srv", "tool", limit=3)
        assert captures[0].schema_id == first
        assert captures[1].schema_id == second
    finally:
        store.close()

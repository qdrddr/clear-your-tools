"""Unit tests for tool example capture recording."""

from __future__ import annotations

from pathlib import Path

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tool_examples.record import record_tool_examples_capture
from cyt.tool_examples.store import ToolExamplesStore


def _config(db_path: Path, workspace: Path, *, enabled: bool = True) -> dict:
    return set_hook_workspace_in_config(
        {
            "tools": {
                "examples": {
                    "enabled": enabled,
                    "database": {"path": str(db_path)},
                },
            },
        },
        workspace,
    )


def test_record_returns_none_when_disabled(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    db = tmp_path / "tool_examples.db"
    config = _config(db, root, enabled=False)
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    assert (
        record_tool_examples_capture(
            workspace=root,
            mcp_server="srv",
            tool_name="search",
            input_schema=schema,
            args={"query": "x"},
            config=config,
        )
        is None
    )


def test_record_uses_workspace_root_outside_git(tmp_path: Path) -> None:
    workspace = tmp_path / "not-a-repo"
    workspace.mkdir()
    db = tmp_path / "tool_examples.db"
    config = _config(db, workspace)
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    schema_id = record_tool_examples_capture(
        workspace=workspace,
        mcp_server="srv",
        tool_name="search",
        input_schema=schema,
        args={"query": "x"},
        config=config,
    )
    assert schema_id is not None
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(workspace.resolve()))
        captures = store.list_captures(project_id, "srv", "search")
        assert len(captures) == 1
        assert captures[0].input_json == {"query": "x"}
    finally:
        store.close()


def test_record_applies_redaction_patterns(tmp_path: Path) -> None:
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
                    "redact_key_patterns": ["(?i)api_key"],
                },
            },
        },
        root,
    )
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "api_key": {"type": "string"},
        },
    }
    schema_id = record_tool_examples_capture(
        workspace=root,
        mcp_server="srv",
        tool_name="search",
        input_schema=schema,
        args={"query": "safe", "api_key": "secret"},
        config=config,
    )
    assert schema_id is not None
    store = ToolExamplesStore.open(str(db))
    try:
        rows = store.list_examples_for_path([schema_id], "inputSchema.properties.query")
        assert len(rows) == 1
        secret_rows = store.list_examples_for_path([schema_id], "inputSchema.properties.api_key")
        assert secret_rows == []
    finally:
        store.close()

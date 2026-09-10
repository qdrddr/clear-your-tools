"""Unit tests for tool example injection enrichment."""

from __future__ import annotations

from pathlib import Path

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tool_examples.enrich import enrich_tools_with_examples
from cyt.tool_examples.store import ToolExamplesStore


def _config(db_path: Path, workspace: Path) -> dict:
    return set_hook_workspace_in_config(
        {
            "tools": {
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db_path)},
                    "inject": {
                        "max_per_property": 3,
                        "max_value_chars": 120,
                        "full_call_examples": True,
                        "max_full_call_examples": 2,
                    },
                },
            },
        },
        workspace,
    )


def test_enrich_appends_property_examples(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    db = tmp_path / "tool_examples.db"
    config = _config(db, root)
    schema = {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Project name"},
            "query": {"type": "string", "description": "Search query"},
        },
    }
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(root))
        store.upsert_capture(
            project_id,
            "codebase-memory-mcp",
            "search_graph",
            schema,
            {"project": "clear-your-tools", "query": "bm25 ranking"},
        )
        store.upsert_capture(
            project_id,
            "codebase-memory-mcp",
            "search_graph",
            schema,
            {"project": "other-repo", "query": "settings"},
        )
    finally:
        store.close()

    tool = {
        "name": "codebase-memory-mcp_search_graph",
        "server_key": "codebase-memory-mcp",
        "tool_name": "search_graph",
        "description": "Search the graph",
        "input_schema": schema,
    }
    enriched = enrich_tools_with_examples([tool], "bm25 ranking project", config)
    props = enriched[0]["input_schema"]["properties"]
    project_desc = props["project"]["description"]
    query_desc = props["query"]["description"]
    assert "Examples:" in project_desc
    assert "clear-your-tools" in project_desc
    assert "Examples:" in query_desc
    assert "bm25" in query_desc.lower() or "ranking" in query_desc.lower()
    assert "Full-call examples:" in enriched[0]["description"]


def test_enrich_noop_when_disabled(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    config = set_hook_workspace_in_config({"tools": {"examples": {"enabled": False}}}, root)
    tool = {
        "name": "srv_tool",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string", "description": "Q"}}},
    }
    enriched = enrich_tools_with_examples([tool], "hello", config)
    assert enriched[0]["input_schema"]["properties"]["q"]["description"] == "Q"


def test_enrich_cross_schema_fallback(tmp_path: Path) -> None:
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
                    "inject": {
                        "cross_schema_fallback": True,
                        "full_call_examples": False,
                    },
                },
            },
        },
        root,
    )
    schema_v1 = {"type": "object", "properties": {"query": {"type": "string", "description": "Q"}}}
    schema_v2 = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Q"},
            "limit": {"type": "integer", "description": "Limit"},
        },
    }
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(root))
        store.upsert_capture(
            project_id,
            "codebase-memory-mcp",
            "search_graph",
            schema_v1,
            {"query": "legacy-value"},
        )
    finally:
        store.close()

    tool = {
        "name": "codebase-memory-mcp_search_graph",
        "server_key": "codebase-memory-mcp",
        "tool_name": "search_graph",
        "input_schema": schema_v2,
    }
    enriched = enrich_tools_with_examples([tool], "legacy query", config)
    desc = enriched[0]["input_schema"]["properties"]["query"]["description"]
    assert "legacy-value" in desc


def test_enrich_truncates_long_values(tmp_path: Path) -> None:
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
                    "inject": {
                        "max_per_property": 1,
                        "max_value_chars": 20,
                        "full_call_examples": False,
                    },
                },
            },
        },
        root,
    )
    schema = {"type": "object", "properties": {"path": {"type": "string", "description": "Path"}}}
    long_path = "/" + ("segment/" * 30)
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(root))
        store.upsert_capture(
            project_id,
            "filesystem",
            "read_file",
            schema,
            {"path": long_path},
        )
    finally:
        store.close()

    tool = {
        "name": "filesystem_read_file",
        "server_key": "filesystem",
        "tool_name": "read_file",
        "input_schema": schema,
    }
    enriched = enrich_tools_with_examples([tool], "read file path", config)
    desc = enriched[0]["input_schema"]["properties"]["path"]["description"]
    assert "..." in desc
    assert len(desc) < len(long_path) + 30


def test_enrich_noop_without_git_project(tmp_path: Path) -> None:
    workspace = tmp_path / "not-a-repo"
    workspace.mkdir()
    db = tmp_path / "tool_examples.db"
    config = _config(db, workspace)
    tool = {
        "name": "srv_tool",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string", "description": "Q"}}},
    }
    enriched = enrich_tools_with_examples([tool], "hello", config)
    assert enriched[0]["input_schema"]["properties"]["q"]["description"] == "Q"

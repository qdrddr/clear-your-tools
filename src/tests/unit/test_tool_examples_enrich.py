"""Unit tests for tool example injection enrichment."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.tool_examples.enrich import enrich_tools_with_examples
from cyt.tool_examples.store import ToolExamplesStore
from cyt.tools.inject import format_tool_item
from cyt.tools.serialize import format_example_line


def _config(db_path: Path, workspace: Path) -> dict:
    return set_hook_workspace_in_config(
        {
            "tools": {
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db_path)},
                    "inject": {
                        "max_value_chars": 120,
                        "full_call_examples": True,
                        "max_full_call_examples": 2,
                        "ranking": {"diversity_threshold": 0.0},
                    },
                },
            },
        },
        workspace,
    )


def test_enrich_attaches_full_call_examples(tmp_path: Path) -> None:
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
    assert props["project"]["description"] == "Project name"
    assert props["query"]["description"] == "Search query"
    assert enriched[0]["description"] == "Search the graph"
    examples = enriched[0]["cyt_injection_examples"]
    assert len(examples) == 2
    assert {"project": "clear-your-tools", "query": "bm25 ranking"} in examples
    assert {"project": "other-repo", "query": "settings"} in examples


def test_enrich_noop_when_disabled(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    config = set_hook_workspace_in_config({"tools": {"examples": {"enabled": False}}}, root)
    tool = {
        "name": "srv_tool",
        "input_schema": {
            "type": "object",
            "properties": {"q": {"type": "string", "description": "Q"}},
        },
    }
    enriched = enrich_tools_with_examples([tool], "hello", config)
    assert enriched[0]["input_schema"]["properties"]["q"]["description"] == "Q"
    assert "cyt_injection_examples" not in enriched[0]


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
    assert enriched[0]["input_schema"]["properties"]["query"]["description"] == "Q"
    assert "cyt_injection_examples" not in enriched[0]


def test_format_example_line_truncates_long_payload() -> None:
    long_path = "/" + ("segment/" * 30)
    line = format_example_line({"path": long_path}, max_chars=20)
    assert line.startswith("- ")
    assert line.endswith("...")


def test_enrich_prefers_diverse_high_usage_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support.tool_examples_fixtures import install_staggered_capture_clock

    install_staggered_capture_clock(monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    db = tmp_path / "tool_examples.db"
    config = set_hook_workspace_in_config(
        {
            "tools": {
                "sequence": ["bm25"],
                "examples": {
                    "enabled": True,
                    "database": {"path": str(db)},
                    "inject": {
                        "max_full_call_examples": 3,
                        "ranking": {"pipeline": ["bm25"], "diversity_threshold": 0.6},
                    },
                },
            },
        },
        root,
    )
    schema = {"type": "object", "properties": {"city": {"type": "string", "description": "City"}}}
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(root))
        for _ in range(3):
            store.upsert_capture(
                project_id,
                "geo",
                "lookup",
                schema,
                {"city": "Chicago, IL"},
            )
        for _ in range(5):
            store.upsert_capture(
                project_id,
                "geo",
                "lookup",
                schema,
                {"city": "New York, NY"},
            )
        store.upsert_capture(project_id, "geo", "lookup", schema, {"city": "Chicago"})
        store.upsert_capture(project_id, "geo", "lookup", schema, {"city": "Chicago, Illinois"})
        store.upsert_capture(project_id, "geo", "lookup", schema, {"city": "San Francisco, CA"})
    finally:
        store.close()

    tool = {
        "name": "geo_lookup",
        "server_key": "geo",
        "tool_name": "lookup",
        "input_schema": schema,
    }
    enriched = enrich_tools_with_examples([tool], "city address lookup", config)
    examples = enriched[0]["cyt_injection_examples"]
    assert len(examples) == 3
    cities = [example["city"] for example in examples]
    assert len(set(cities)) == 3
    assert all(isinstance(example, dict) and "city" in example for example in examples)


def test_format_tool_item_renders_examples_block(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    db = tmp_path / "tool_examples.db"
    config = _config(db, root)
    schema = {
        "type": "object",
        "properties": {"source": {"type": "string"}},
        "required": ["source"],
    }
    store = ToolExamplesStore.open(str(db))
    try:
        project_id = store.get_or_create_project(str(root))
        store.upsert_capture(
            project_id,
            "context-mode",
            "ctx_index",
            schema,
            {"path": "/path/to/large-spec.md", "source": "openapi-v2-spec"},
        )
    finally:
        store.close()

    tool = {
        "name": "context-mode_ctx_index",
        "server_key": "context-mode",
        "tool_name": "ctx_index",
        "description": "Index content",
        "input_schema": schema,
    }
    enriched = enrich_tools_with_examples([tool], "openapi spec path", config)[0]
    item = format_tool_item(enriched)
    assert "<examples>" in item
    assert "</examples>" in item
    assert "- {'path':'/path/to/large-spec.md','source':'openapi-v2-spec'}" in item
    assert "Examples:" not in item


def test_enrich_noop_without_git_project(tmp_path: Path) -> None:
    workspace = tmp_path / "not-a-repo"
    workspace.mkdir()
    db = tmp_path / "tool_examples.db"
    config = _config(db, workspace)
    tool = {
        "name": "srv_tool",
        "input_schema": {
            "type": "object",
            "properties": {"q": {"type": "string", "description": "Q"}},
        },
    }
    enriched = enrich_tools_with_examples([tool], "hello", config)
    assert enriched[0]["input_schema"]["properties"]["q"]["description"] == "Q"

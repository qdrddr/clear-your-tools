"""Tests for agent-visible Executor tool name reconstruction."""

from __future__ import annotations

from unittest.mock import patch

from cyt.executor.tool_names import agent_visible_tool_name
from cyt.tools.inject import format_tool_item


def test_agent_visible_tool_name_dynamic_with_metadata() -> None:
    tool = {
        "name": "tools.code_review_graph_mcp.org.localcodereviewgraph.apply_refactor_tool",
        "owner": "org",
        "integration": "code_review_graph_mcp",
        "connection": "localcodereviewgraph",
        "tool_name": "apply_refactor_tool",
    }
    assert (
        agent_visible_tool_name(tool)
        == "code_review_graph_mcp.org.localcodereviewgraph.apply_refactor_tool"
    )


def test_agent_visible_tool_name_static_executor_unchanged() -> None:
    tool = {
        "name": "executor.coreTools.connections.list",
        "integration": "executor",
        "static": True,
    }
    assert agent_visible_tool_name(tool) == "executor.coreTools.connections.list"


def test_agent_visible_tool_name_proxy_mcp_unchanged() -> None:
    tool = {"name": "mcp__demo__tool", "description": "Demo", "input_schema": {}}
    assert agent_visible_tool_name(tool) == "mcp__demo__tool"


def test_agent_visible_tool_name_incomplete_metadata_unchanged() -> None:
    tool = {"name": "tools.demo.org.default.search"}
    assert agent_visible_tool_name(tool) == "tools.demo.org.default.search"


def test_agent_visible_tool_name_does_not_touch_executor_catalog() -> None:
    tool = {
        "name": "tools.demo.org.default.search",
        "owner": "org",
        "integration": "demo",
        "connection": "default",
        "tool_name": "search",
    }
    with patch(
        "cyt.executor.http.get_executor_catalog",
        side_effect=AssertionError("must not fetch catalog during name rewrite"),
    ):
        assert agent_visible_tool_name(tool) == "demo.org.default.search"


def test_format_tool_item_uses_agent_visible_executor_name() -> None:
    tool = {
        "name": "tools.semble_mcp.org.default.search",
        "owner": "org",
        "integration": "semble_mcp",
        "connection": "default",
        "tool_name": "search",
        "description": "Search the codebase",
        "input_schema": {"type": "object", "properties": {}},
    }
    item = format_tool_item(tool)
    assert "<tool name='semble_mcp.org.default.search' description='Search the codebase'>" in item

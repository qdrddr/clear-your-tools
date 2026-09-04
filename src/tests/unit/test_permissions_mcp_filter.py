"""Tests for MCP permission matching and runtime filters."""

from __future__ import annotations

from fastmcp.tools.base import Tool

from cyt.permissions.match import (
    is_catalog_tool_denied,
    is_mcp_server_denied,
    is_mcp_tool_denied,
)
from cyt.permissions.runtime import filter_catalog_tool_dicts, filter_mcp_servers
from cyt_mcp.catalog_build import build_catalog_from_tools


def test_mcp_deny_rules_match_server_tool_and_wildcard() -> None:
    deny = ("server-a", "server-b/*", "server-c/tool-one")
    assert is_mcp_server_denied("server-a", deny)
    assert not is_mcp_server_denied("server-z", deny)
    assert is_mcp_tool_denied("server-b", "anything", deny)
    assert is_mcp_tool_denied("server-c", "tool-one", deny)
    assert not is_mcp_tool_denied("server-c", "tool-two", deny)
    assert is_catalog_tool_denied("server-c_tool-one", deny)


def test_filter_mcp_servers_and_catalog_tools() -> None:
    servers: dict[str, object] = {"allowed": {}, "blocked": {}}
    filtered_servers = filter_mcp_servers(servers, ("blocked",))
    assert list(filtered_servers) == ["allowed"]

    tools = [
        {"name": "allowed_tool"},
        {"name": "blocked_tool"},
    ]
    filtered_tools = filter_catalog_tool_dicts(tools, ("blocked",))
    assert [tool["name"] for tool in filtered_tools] == ["allowed_tool"]


def test_build_catalog_from_tools_respects_deny_entries() -> None:
    catalog, index = build_catalog_from_tools(
        [
            Tool.from_function(lambda: None, name="allowed_x"),
            Tool.from_function(lambda: None, name="blocked_y"),
        ],
        deny_entries=("blocked",),
    )
    assert [entry["name"] for entry in catalog] == ["allowed_x"]
    assert "blocked_y" not in index

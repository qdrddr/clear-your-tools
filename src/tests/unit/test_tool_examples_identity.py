"""Unit tests for MCP server/tool identity resolution."""

from __future__ import annotations

from cyt.tool_examples.identity import (
    resolve_mcp_server_and_tool,
    resolve_mcp_server_and_tool_from_wire_name,
)


def test_resolve_from_server_key_and_tool_name() -> None:
    tool = {
        "name": "codebase-memory-mcp_search_graph",
        "server_key": "codebase-memory-mcp",
        "tool_name": "search_graph",
    }
    assert resolve_mcp_server_and_tool(tool) == ("codebase-memory-mcp", "search_graph")


def test_resolve_from_prefixed_name() -> None:
    tool = {"name": "jcodemunch_search_symbols"}
    assert resolve_mcp_server_and_tool(tool) == ("jcodemunch", "search_symbols")


def test_resolve_from_wire_name() -> None:
    assert resolve_mcp_server_and_tool_from_wire_name("demo_search") == ("demo", "search")


def test_resolve_empty_wire_name() -> None:
    assert resolve_mcp_server_and_tool_from_wire_name("") == ("unknown", "unknown")

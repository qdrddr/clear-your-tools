"""Unit tests for MCP server/tool identity resolution."""

from __future__ import annotations

from cyt.tool_examples.identity import (
    is_valid_tool_example_identity,
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


def test_resolve_infers_bare_name_when_session_log_omits_tool_name() -> None:
    tool = {"name": "grep", "server_key": "fff"}
    assert resolve_mcp_server_and_tool(tool) == ("fff", "grep")


def test_resolve_without_explicit_fields_returns_unknown() -> None:
    tool = {"name": "jcodemunch_search_symbols"}
    assert resolve_mcp_server_and_tool(tool) == ("unknown", "jcodemunch_search_symbols")
    assert not is_valid_tool_example_identity(*resolve_mcp_server_and_tool(tool))


def test_resolve_from_wire_name_requires_catalog_mapping() -> None:
    assert resolve_mcp_server_and_tool_from_wire_name("demo_search") == (
        "unknown",
        "demo_search",
    )


def test_resolve_empty_wire_name() -> None:
    assert resolve_mcp_server_and_tool_from_wire_name("") == ("unknown", "unknown")
    assert not is_valid_tool_example_identity("unknown", "unknown")

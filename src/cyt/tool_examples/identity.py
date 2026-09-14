"""Resolve MCP server and tool names from catalog tool dicts."""

from __future__ import annotations

from typing import Any

_INVALID_IDENTITY_MARKERS = frozenset({"", "unknown"})


def is_valid_tool_example_identity(mcp_server: str, tool_name: str) -> bool:
    """True when both backend server and tool names are non-empty and not placeholder values."""
    server = str(mcp_server or "").strip()
    bare = str(tool_name or "").strip()
    return (
        bool(server)
        and bool(bare)
        and server.casefold() not in _INVALID_IDENTITY_MARKERS
        and bare.casefold() not in _INVALID_IDENTITY_MARKERS
    )


def resolve_mcp_server_and_tool(tool: dict[str, Any]) -> tuple[str, str]:
    """Return backend identity from explicit catalog fields (server_key, tool_name)."""
    from cyt_mcp.tool_identity import resolve_backend_identity

    return resolve_backend_identity(tool)


def resolve_mcp_server_and_tool_from_wire_name(wire_name: str) -> tuple[str, str]:
    """Wire names must be resolved through the cyt-mcp catalog mapping, not string heuristics."""
    name = wire_name.strip()
    if not name:
        return "unknown", "unknown"
    return "unknown", name

"""Resolve MCP server and tool names from catalog tool dicts."""

from __future__ import annotations

from typing import Any


def resolve_mcp_server_and_tool(tool: dict[str, Any]) -> tuple[str, str]:
    """Return backend identity from explicit catalog fields (server_key, tool_name)."""
    server = str(tool.get("server_key") or tool.get("mcp_server") or "").strip()
    bare = str(tool.get("tool_name") or "").strip()
    if server and bare:
        return server, bare
    wire = str(tool.get("name") or "").strip()
    return "unknown", wire or "unknown"


def resolve_mcp_server_and_tool_from_wire_name(wire_name: str) -> tuple[str, str]:
    """Wire names must be resolved through the cyt-mcp catalog mapping, not string heuristics."""
    name = wire_name.strip()
    if not name:
        return "unknown", "unknown"
    return "unknown", name

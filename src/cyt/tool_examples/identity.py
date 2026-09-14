"""Resolve MCP server and tool names from catalog tool dicts."""

from __future__ import annotations

from typing import Any


def resolve_mcp_server_and_tool(tool: dict[str, Any]) -> tuple[str, str]:
    server = str(tool.get("server_key") or tool.get("mcp_server") or "").strip()
    bare = str(tool.get("tool_name") or "").strip()
    name = str(tool.get("name") or "").strip()
    if server and bare:
        return server, bare
    if server and name:
        if name.startswith(f"{server}_"):
            return server, name[len(server) + 1 :]
        return server, name
    if name and "_" in name:
        head, _, tail = name.partition("_")
        if head and tail:
            return head, tail
    return server or "unknown", bare or name or "unknown"


def resolve_mcp_server_and_tool_from_wire_name(wire_name: str) -> tuple[str, str]:
    name = wire_name.strip()
    if not name:
        return "unknown", "unknown"
    if "_" in name:
        head, _, tail = name.partition("_")
        if head and tail:
            return head, tail
    return "unknown", name

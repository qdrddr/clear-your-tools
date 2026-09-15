"""Resolve MCP server and tool names from catalog tool dicts."""

from __future__ import annotations

from pathlib import Path
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


def resolve_mcp_server_and_tool(
    tool: dict[str, Any],
    *,
    server_keys: list[str] | None = None,
    project_root: Path | str | None = None,
) -> tuple[str, str]:
    """Return canonical backend identity when server keys are available."""
    from cyt_mcp.config import load_known_mcp_server_keys
    from cyt_mcp.tool_identity import canonical_backend_identity, resolve_backend_identity

    keys = list(server_keys) if server_keys is not None else []
    if not keys:
        keys = load_known_mcp_server_keys(project_root=project_root)
    explicit_server = str(tool.get("server_key") or tool.get("mcp_server") or "").strip()
    if explicit_server and explicit_server not in keys:
        keys = sorted(set(keys) | {explicit_server}, key=len, reverse=True)
    if keys:
        return canonical_backend_identity(tool, keys)
    return resolve_backend_identity(tool)


def resolve_mcp_server_and_tool_from_wire_name(wire_name: str) -> tuple[str, str]:
    """Wire names must be resolved through the cyt-mcp catalog mapping, not string heuristics."""
    name = wire_name.strip()
    if not name:
        return "unknown", "unknown"
    return "unknown", name

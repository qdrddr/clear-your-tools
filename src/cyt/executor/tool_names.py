"""Agent-visible Executor tool names for hook injection."""

from __future__ import annotations

from typing import Any


def agent_visible_tool_name(tool: dict[str, Any]) -> str:
    """Return the tool name agents should use for MCP routing.

    Reads metadata already merged onto cached catalog tool dicts; no HTTP or cache lookups.
    """
    name = str(tool.get("name") or "").strip()
    if not name:
        return name

    if tool.get("static") is True:
        return name
    if str(tool.get("integration") or "") == "executor":
        return name
    if name.startswith("executor."):
        return name

    integration = str(tool.get("integration") or "").strip()
    owner = str(tool.get("owner") or "").strip()
    connection = str(tool.get("connection") or "").strip()
    tool_name = str(tool.get("tool_name") or "").strip()

    if integration and owner and connection and tool_name:
        return f"{integration}.{owner}.{connection}.{tool_name}"

    return name

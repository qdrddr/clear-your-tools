"""cyt-mcp_search tool: on-demand full backend tool definitions."""

from __future__ import annotations

from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool

from cyt_mcp.runtime_cache import RuntimeToolCache

# Canonical name used by hooks, session logs, and tool-gate normalization.
SEARCH_TOOL_NAME = "cyt-mcp_search"
# MCP stdio wire name. Must not share the cyt-mcp server prefix or Cursor drops it from tools/list.
MCP_WIRE_SEARCH_TOOL_NAME = "search"

_SEARCH_TOOL_BASE_DESCRIPTION = (
    "Return the full MCP tool definition for a cyt-mcp backend tool by name. "
    "Use when hook-injected stubs lack properties or metadata you need. "
    "The tool_name argument must be one of the backend tools exposed by this server."
)

_CURSOR_SEARCH_NOTE = (
    " Read `.cursor/rules/cyt-injection.mdc` for pruned relevant tool definitions."
)


def search_tool_description(*, agent: str | None) -> str:
    description = _SEARCH_TOOL_BASE_DESCRIPTION
    if agent == "cursor":
        description = f"{description}{_CURSOR_SEARCH_NOTE}"
    return description


def build_search_input_schema(allowed_names: list[str]) -> dict[str, Any]:
    enum_values = sorted(
        {
            name
            for name in allowed_names
            if name and name not in {SEARCH_TOOL_NAME, MCP_WIRE_SEARCH_TOOL_NAME}
        },
    )
    return {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Backend cyt-mcp tool name to look up.",
                "enum": enum_values,
            },
        },
        "required": ["tool_name"],
        "additionalProperties": False,
    }


def lookup_tool_definition(cache: RuntimeToolCache, tool_name: str) -> dict[str, Any]:
    name = str(tool_name or "").strip()
    if not name:
        raise ValueError("tool_name is required")
    if name == SEARCH_TOOL_NAME:
        raise ValueError(f"{SEARCH_TOOL_NAME} cannot look up itself")
    allowed = {entry.get("name") for entry in cache.snapshot()}
    if name not in allowed:
        raise ValueError(f"unknown tool: {name!r}")
    definition = cache.search_index_entry(name)
    if definition is None:
        raise ValueError(f"tool {name!r} is not available in the search index")
    return dict(definition)


def _search_handler(cache: RuntimeToolCache, tool_name: str) -> dict[str, Any]:
    return lookup_tool_definition(cache, tool_name)


def register_search_tool(
    server: FastMCP,
    cache: RuntimeToolCache,
    *,
    agent: str | None,
) -> Tool:
    tool = Tool.from_function(
        lambda tool_name: _search_handler(cache, tool_name),
        name=MCP_WIRE_SEARCH_TOOL_NAME,
    )
    tool = tool.model_copy(update={"description": search_tool_description(agent=agent)})
    cast(Any, server).add_tool(tool)
    cache.set_search_tool(tool)
    return tool


def refresh_search_tool_schema(cache: RuntimeToolCache) -> None:
    tool = cache.search_tool()
    if tool is None:
        return
    allowed = [str(entry.get("name") or "") for entry in cache.snapshot()]
    schema = build_search_input_schema(allowed)
    mcp_tool = tool.to_mcp_tool()
    updated = tool.model_copy(
        update={
            "parameters": schema,
            "description": mcp_tool.description or search_tool_description(agent=None),
        },
    )
    cache.set_search_tool(updated)

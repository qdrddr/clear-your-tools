"""Build hook-daemon catalog and search index from backend FastMCP tools."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool
from mcp.types import Tool as McpWireTool

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import SEARCH_TOOL_NAME, refresh_search_tool_schema


def mcp_tool_to_catalog_dict(mcp_tool: McpWireTool) -> dict[str, Any]:
    schema_dict = dict(mcp_tool.inputSchema)
    entry: dict[str, Any] = {
        "name": str(mcp_tool.name),
        "inputSchema": schema_dict,
    }
    if mcp_tool.description:
        entry["description"] = str(mcp_tool.description)
    title = getattr(mcp_tool, "title", None)
    if title:
        entry["title"] = str(title)
    return entry


def mcp_tool_to_search_index_entry(mcp_tool: McpWireTool) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": str(mcp_tool.name),
        "inputSchema": dict(mcp_tool.inputSchema),
    }
    if mcp_tool.description:
        entry["description"] = str(mcp_tool.description)
    title = getattr(mcp_tool, "title", None)
    if title:
        entry["title"] = str(title)
    output_schema = getattr(mcp_tool, "outputSchema", None)
    if output_schema is not None:
        entry["outputSchema"] = output_schema
    if mcp_tool.annotations is not None:
        entry["annotations"] = mcp_tool.annotations
    if mcp_tool.execution is not None:
        entry["execution"] = mcp_tool.execution
    if mcp_tool.meta is not None:
        entry["meta"] = mcp_tool.meta
    return entry


def build_catalog_from_tools(
    tools: Sequence[Tool],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    catalog_entries: list[dict[str, Any]] = []
    search_index: dict[str, dict[str, Any]] = {}
    for tool in tools:
        mcp_tool = cast(McpWireTool, tool.to_mcp_tool())
        name = str(mcp_tool.name)
        if name == SEARCH_TOOL_NAME:
            continue
        catalog_entries.append(mcp_tool_to_catalog_dict(mcp_tool))
        search_index[name] = mcp_tool_to_search_index_entry(mcp_tool)
    return catalog_entries, search_index


async def refresh_catalog_cache(server: FastMCP, cache: RuntimeToolCache) -> None:
    """Populate hook-daemon catalog + search index from raw backend tools."""
    backend_server = cast(Any, server)
    backend_tools = await backend_server._list_tools()
    catalog_entries, search_index = build_catalog_from_tools(backend_tools)
    cache.replace(catalog_entries, search_index=search_index)
    refresh_search_tool_schema(cache)

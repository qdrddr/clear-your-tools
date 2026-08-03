"""Stub projection for frontend tools/list."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastmcp.server.transforms import Transform
from fastmcp.tools.tool import Tool

from cyt_mcp.runtime_cache import RuntimeToolCache

_MINIMAL_OBJECT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def _mcp_tool_to_full_dict(mcp_tool: Tool) -> dict[str, Any]:
    schema = mcp_tool.inputSchema
    if isinstance(schema, dict):
        schema_dict = dict(schema)
    else:
        schema_dict = {}
    entry: dict[str, Any] = {
        "name": str(mcp_tool.name),
        "inputSchema": schema_dict,
    }
    if mcp_tool.description:
        entry["description"] = str(mcp_tool.description)
    if mcp_tool.title:
        entry["title"] = str(mcp_tool.title)
    return entry


def _stub_from_tool(tool: Tool, *, include_description: bool) -> Tool:
    mcp_tool = tool.to_mcp_tool()
    updates: dict[str, Any] = {"parameters": dict(_MINIMAL_OBJECT_SCHEMA)}
    if not include_description:
        updates["description"] = ""
    stub = Tool.from_tool(
        tool,
        description=(mcp_tool.description or "") if include_description else "",
    )
    return stub.model_copy(update=updates)


class StubListTransform(Transform):
    """Cache full FastMCP tools and expose minimal stubs to MCP clients."""

    def __init__(self, cache: RuntimeToolCache, *, include_description: bool = False) -> None:
        self._cache = cache
        self._include_description = include_description

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        full_entries: list[dict[str, Any]] = []
        stubs: list[Tool] = []
        for tool in tools:
            mcp_tool = tool.to_mcp_tool()
            full_entries.append(_mcp_tool_to_full_dict(mcp_tool))
            stub = _stub_from_tool(tool, include_description=self._include_description)
            stubs.append(stub.model_copy(update={"output_schema": None}))
        self._cache.replace(full_entries)
        return stubs

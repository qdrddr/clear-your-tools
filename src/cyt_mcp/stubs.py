"""Stub projection for frontend tools/list."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastmcp.server.transforms import Transform
from fastmcp.tools.tool import Tool

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME

_MINIMAL_OBJECT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


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
    """Expose minimal backend stubs and full cyt-mcp_search to MCP clients."""

    def __init__(self, cache: RuntimeToolCache, *, include_description: bool = False) -> None:
        self._cache = cache
        self._include_description = include_description

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        stubs: list[Tool] = []
        for tool in tools:
            mcp_tool = tool.to_mcp_tool()
            name = str(mcp_tool.name)
            if name == MCP_WIRE_SEARCH_TOOL_NAME:
                refreshed = self._cache.search_tool()
                search_stub = (refreshed or tool).model_copy(update={"output_schema": None})
                stubs.append(search_stub)
                continue
            stub = _stub_from_tool(tool, include_description=self._include_description)
            stubs.append(stub.model_copy(update={"output_schema": None}))
        return stubs

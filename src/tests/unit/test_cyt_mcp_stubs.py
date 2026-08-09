"""Unit tests for cyt_mcp stub projection and catalog build."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from fastmcp.tools.base import Tool

from cyt_mcp.catalog_build import build_catalog_from_tools
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import (
    MCP_WIRE_SEARCH_TOOL_NAME,
    refresh_search_tool_schema,
    register_search_tool,
)
from cyt_mcp.stubs import StubListTransform, _stub_from_tool


def test_stub_minimal_schema() -> None:
    tool = Tool.from_function(lambda path: path, name="filesystem_read_file")
    stub = _stub_from_tool(tool, include_description=False)
    mcp = stub.to_mcp_tool()
    assert mcp.inputSchema == {"type": "object", "properties": {}}


def test_build_catalog_excludes_search_tool() -> None:
    backend = Tool.from_function(lambda project: project, name="codebase-memory-mcp_query_graph")
    search = Tool.from_function(lambda tool_name: tool_name, name=MCP_WIRE_SEARCH_TOOL_NAME)
    catalog, search_index = build_catalog_from_tools([backend, search])
    names = [entry["name"] for entry in catalog]
    assert MCP_WIRE_SEARCH_TOOL_NAME not in names
    assert "codebase-memory-mcp_query_graph" in names
    assert "codebase-memory-mcp_query_graph" in search_index


def test_stub_list_transform_does_not_populate_catalog() -> None:
    cache = RuntimeToolCache()
    transform = StubListTransform(cache, include_description=False)
    tool = Tool.from_function(lambda: None, name="demo_tool")
    stubs = asyncio.run(transform.list_tools([tool]))
    assert len(stubs) == 1
    assert cache.snapshot() == []


def test_stub_list_uses_refreshed_search_tool_enum() -> None:
    cache = RuntimeToolCache()
    server = FastMCP("cyt-mcp-test")
    register_search_tool(server, cache, agent="cursor")
    backend = Tool.from_function(lambda project: project, name="codebase-memory-mcp_search_graph")
    cache.replace(
        [
            {
                "name": "codebase-memory-mcp_search_graph",
                "inputSchema": {
                    "type": "object",
                    "properties": {"project": {"type": "string"}},
                },
            },
        ],
        search_index={
            "codebase-memory-mcp_search_graph": {
                "name": "codebase-memory-mcp_search_graph",
                "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}},
            },
        },
    )
    refresh_search_tool_schema(cache)
    search_tool = cache.search_tool()
    assert search_tool is not None

    transform = StubListTransform(cache, include_description=False)
    stubs = asyncio.run(transform.list_tools([search_tool, backend]))
    search_stub = next(
        stub for stub in stubs if stub.to_mcp_tool().name == MCP_WIRE_SEARCH_TOOL_NAME
    )
    enum_values = search_stub.to_mcp_tool().inputSchema["properties"]["tool_name"]["enum"]
    assert "codebase-memory-mcp_search_graph" in enum_values

    backend_stub = next(
        stub for stub in stubs if stub.to_mcp_tool().name == "codebase-memory-mcp_search_graph"
    )
    assert backend_stub.to_mcp_tool().inputSchema == {"type": "object", "properties": {}}

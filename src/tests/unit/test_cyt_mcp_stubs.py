"""Unit tests for cyt_mcp stub projection."""

from __future__ import annotations

from fastmcp.tools.tool import Tool

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.stubs import StubListTransform, _stub_from_tool


def test_stub_minimal_schema() -> None:
    tool = Tool.from_function(lambda path: path, name="filesystem_read_file")
    stub = _stub_from_tool(tool, include_description=False)
    mcp = stub.to_mcp_tool()
    assert mcp.inputSchema == {"type": "object", "properties": {}}


def test_stub_list_transform_populates_cache() -> None:
    import asyncio

    cache = RuntimeToolCache()
    transform = StubListTransform(cache, include_description=False)
    tool = Tool.from_function(lambda: None, name="demo_tool")
    stubs = asyncio.run(transform.list_tools([tool]))
    assert len(stubs) == 1
    snapshot = cache.snapshot()
    assert snapshot[0]["name"] == "demo_tool"
    assert snapshot[0]["inputSchema"]

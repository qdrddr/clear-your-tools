"""Unit tests for cyt-mcp hook-daemon catalog export."""

from __future__ import annotations

from cyt_mcp.catalog import catalog_payload
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import SEARCH_TOOL_NAME


def test_catalog_payload_uses_full_backend_defs() -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [
            {
                "name": "codebase-memory-mcp_query_graph",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project": {"type": "string"},
                        "query": {"type": "string"},
                    },
                    "required": ["project", "query"],
                },
                "description": "Run cypher",
            },
        ],
    )
    payload = catalog_payload(cache, agent="cursor")
    tool = payload["tools"][0]
    assert tool["name"] == "codebase-memory-mcp_query_graph"
    assert tool["input_schema"]["required"] == ["project", "query"]
    assert tool["cyt_catalog_source"] == "cyt_mcp"


def test_catalog_payload_excludes_search_tool() -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [
            {
                "name": SEARCH_TOOL_NAME,
                "inputSchema": {"type": "object", "properties": {"tool_name": {"type": "string"}}},
            },
            {
                "name": "codebase-memory-mcp_search_graph",
                "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}},
            },
        ],
    )
    names = [tool["name"] for tool in catalog_payload(cache, agent="cursor")["tools"]]
    assert SEARCH_TOOL_NAME not in names
    assert "codebase-memory-mcp_search_graph" in names

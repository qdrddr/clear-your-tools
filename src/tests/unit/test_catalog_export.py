"""Tests for cyt-mcp catalog export and token stats."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from fastmcp.tools.base import Tool

from cyt.indexer.tokens import count_json_tokens
from cyt_mcp.catalog_build import json_safe_value, mcp_tool_to_catalog_dict
from cyt_mcp.catalog_export import (
    catalog_token_stats,
    frontend_payload_from_hook_tools,
    frontend_stubs,
    group_backend_catalog_payload,
    stub_dict_from_hook_tool,
    tool_dicts_from_backend_payload,
)
from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME


def test_stub_dict_from_hook_tool_minimal_schema() -> None:
    tool = {
        "name": "fff_grep",
        "description": "grep files",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
    stub = stub_dict_from_hook_tool(tool, retain={"tool": ["name"], "required_properties": []})
    assert stub["name"] == "fff_grep"
    assert "description" not in stub
    assert stub["inputSchema"] == {"type": "object", "properties": {}}


def test_frontend_payload_from_hook_tools_includes_search_tool() -> None:
    config = sample_aggregator_config()
    hook_tools = [
        {
            "name": "fff_grep",
            "description": "grep files",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
        {
            "name": "fff_find_files",
            "description": "find files",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    ]
    payload = frontend_payload_from_hook_tools(hook_tools, config=config)
    names = [tool["name"] for tool in payload["tools"]]
    assert names[-1] == MCP_WIRE_SEARCH_TOOL_NAME
    assert "fff_grep" in names
    search_tool = payload["tools"][-1]
    assert "fff_grep" in search_tool["inputSchema"]["properties"]["tool_name"]["enum"]


def test_catalog_token_stats_matches_count_json_tokens() -> None:
    tools = [{"name": "demo", "inputSchema": {"type": "object", "properties": {}}}]
    stats = catalog_token_stats(tools)
    assert stats["tool_count"] == 1
    assert stats["tokens_compact_json"] == count_json_tokens(tools)


def test_group_backend_catalog_payload_groups_by_server_key() -> None:
    flat = {
        "agent": "cursor",
        "tools": [
            {
                "name": "context7_query-docs",
                "server_key": "context7",
                "tool_name": "query-docs",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "fff_grep",
                "server_key": "fff",
                "tool_name": "grep",
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
        "degraded_servers": [],
    }
    grouped = group_backend_catalog_payload(
        flat,
        server_origins={"context7": "user", "fff": "workspace"},
        server_keys=["context7", "fff"],
    )
    assert set(grouped["servers"]) == {"context7", "fff"}
    assert grouped["servers"]["context7"]["origin"] == "user"
    assert grouped["servers"]["fff"]["origin"] == "workspace"
    assert grouped["servers"]["context7"]["tools"][0]["name"] == "context7_query-docs"
    flattened = tool_dicts_from_backend_payload(grouped)
    assert {tool["name"] for tool in flattened} == {"context7_query-docs", "fff_grep"}


def test_frontend_stubs_matches_list_tools_wire_format() -> None:
    config = sample_aggregator_config()
    cache = RuntimeToolCache()
    backend_tool = Tool.from_function(
        lambda query: query,
        name="fff_grep",
        description="grep files",
    )
    search_tool = Tool.from_function(
        lambda tool_name: tool_name,
        name=MCP_WIRE_SEARCH_TOOL_NAME,
        description="search",
    )
    server = MagicMock()
    server.list_tools = AsyncMock(return_value=[backend_tool, search_tool])
    expected = [
        json_safe_value(mcp_tool_to_catalog_dict(tool.to_mcp_tool()))
        for tool in [backend_tool, search_tool]
    ]

    payload = asyncio.run(frontend_stubs(server, cache, config))

    assert payload["agent"] == config.agent
    assert payload["tools"] == expected
    server.list_tools.assert_awaited_once_with()


def test_catalog_token_stats_accepts_mcp_annotations() -> None:
    from mcp.types import ToolAnnotations

    tools = [
        {
            "name": "demo",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": ToolAnnotations(title="Demo"),
        },
    ]
    stats = catalog_token_stats(tools)
    assert stats["tool_count"] == 1
    assert stats["tokens_compact_json"] > 0

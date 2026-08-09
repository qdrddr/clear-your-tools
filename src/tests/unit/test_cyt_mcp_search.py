"""Unit tests for cyt-mcp search module."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP
from mcp.types import TextContent

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import (
    SEARCH_TOOL_NAME,
    format_unsupported_parameters_message,
    lookup_tool_definition,
    parse_get_tool_definitions_arguments,
    register_search_tool,
)

_GRAPH_TOOL = "codebase-memory-mcp_search_graph"
_QUERY_TOOL = "codebase-memory-mcp_query_graph"
_TYPO_GRAPH_TOOL = "codebase-memory-mcp_search_grph"


def _graph_schema() -> dict:
    return {
        "type": "object",
        "properties": {"project": {"type": "string"}},
        "required": ["project"],
    }


def test_lookup_returns_full_definition() -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [{"name": _GRAPH_TOOL, "inputSchema": {"type": "object"}}],
        search_index={
            _GRAPH_TOOL: {
                "name": _GRAPH_TOOL,
                "inputSchema": {
                    "type": "object",
                    "properties": {"project": {"type": "string"}},
                },
                "outputSchema": {"type": "object"},
            },
        },
    )
    result = lookup_tool_definition(cache, _GRAPH_TOOL)
    assert result["outputSchema"] == {"type": "object"}


def test_lookup_rejects_self() -> None:
    cache = RuntimeToolCache()
    with pytest.raises(ValueError, match="itself"):
        lookup_tool_definition(cache, SEARCH_TOOL_NAME)


def test_lookup_rejects_unknown_tool() -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [{"name": _GRAPH_TOOL, "inputSchema": _graph_schema()}],
        search_index={_GRAPH_TOOL: {"name": _GRAPH_TOOL, "inputSchema": _graph_schema()}},
    )
    with pytest.raises(ValueError, match="Use one of these tool names"):
        lookup_tool_definition(cache, "totally-unrelated-xyz-tool-name")


def test_parse_rejects_unexpected_arguments() -> None:
    _, error = parse_get_tool_definitions_arguments(
        {"query": "bm25", "limit": 10},
        agent="cursor",
    )
    assert error is not None
    assert "not supported" in error
    assert "`query`" in error
    assert "`limit`" in error
    assert "tool_name" in error
    assert ".cursor/rules/cyt-injection.mdc" in error


def test_format_unsupported_parameters_without_cursor_note() -> None:
    message = format_unsupported_parameters_message(["query"], agent="claude")
    assert "not supported" in message
    assert "cyt-injection" not in message


async def _call_get_tool_definitions(
    cache: RuntimeToolCache,
    arguments: dict,
    *,
    agent: str | None = None,
) -> str:
    server = FastMCP("cyt-mcp-test")
    register_search_tool(server, cache, agent=agent)
    tool = cache.search_tool()
    assert tool is not None
    result = await tool.run(arguments)
    content = result.content[0]
    assert isinstance(content, TextContent)
    return str(content.text)


def test_get_tool_definitions_returns_friendly_error_for_wrong_args() -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [{"name": _GRAPH_TOOL, "inputSchema": _graph_schema()}],
        search_index={_GRAPH_TOOL: {"name": _GRAPH_TOOL, "inputSchema": _graph_schema()}},
    )
    message = asyncio.run(
        _call_get_tool_definitions(
            cache,
            {"query": "bm25 codebase primary location", "limit": 10},
            agent="cursor",
        ),
    )
    assert "not supported" in message
    assert "tool_name" in message
    assert "validation errors" not in message.lower()


def test_get_tool_definitions_lists_available_names_for_unknown_tool() -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [{"name": _GRAPH_TOOL, "inputSchema": _graph_schema()}],
        search_index={_GRAPH_TOOL: {"name": _GRAPH_TOOL, "inputSchema": _graph_schema()}},
    )
    message = asyncio.run(
        _call_get_tool_definitions(cache, {"tool_name": "totally-unrelated-xyz-tool-name"}),
    )
    assert "Use one of these tool names:" in message
    assert _GRAPH_TOOL in message
    assert "unknown tool" not in message


def test_lookup_fuzzy_matches_typo() -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [
            {"name": _GRAPH_TOOL, "inputSchema": _graph_schema()},
            {"name": _QUERY_TOOL, "inputSchema": _graph_schema()},
        ],
        search_index={
            _GRAPH_TOOL: {"name": _GRAPH_TOOL, "inputSchema": _graph_schema()},
            _QUERY_TOOL: {"name": _QUERY_TOOL, "inputSchema": _graph_schema()},
        },
    )
    result = lookup_tool_definition(cache, _TYPO_GRAPH_TOOL)
    assert result["name"] == _GRAPH_TOOL

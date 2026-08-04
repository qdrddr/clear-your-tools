"""Unit tests for cyt-mcp search module."""

from __future__ import annotations

import pytest

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import SEARCH_TOOL_NAME, lookup_tool_definition

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
    with pytest.raises(ValueError, match="unknown tool"):
        lookup_tool_definition(cache, "totally-unrelated-xyz-tool-name")


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

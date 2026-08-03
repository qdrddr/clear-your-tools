"""Unit tests for cyt-mcp search module."""

from __future__ import annotations

import pytest

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import SEARCH_TOOL_NAME, lookup_tool_definition


def test_lookup_returns_full_definition() -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [{"name": "codebase-memory-mcp_search_graph", "inputSchema": {"type": "object"}}],
        search_index={
            "codebase-memory-mcp_search_graph": {
                "name": "codebase-memory-mcp_search_graph",
                "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}},
                "outputSchema": {"type": "object"},
            },
        },
    )
    result = lookup_tool_definition(cache, "codebase-memory-mcp_search_graph")
    assert result["outputSchema"] == {"type": "object"}


def test_lookup_rejects_self() -> None:
    cache = RuntimeToolCache()
    with pytest.raises(ValueError, match="itself"):
        lookup_tool_definition(cache, SEARCH_TOOL_NAME)


def test_lookup_rejects_unknown_tool() -> None:
    cache = RuntimeToolCache()
    with pytest.raises(ValueError, match="unknown tool"):
        lookup_tool_definition(cache, "missing-tool")

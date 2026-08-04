"""Unit tests for cyt-mcp tool name fuzzy resolution."""

from __future__ import annotations

from cyt_mcp.tool_name_fuzzy import fuzzy_resolve_tool_name

_GRAPH_TOOL = "codebase-memory-mcp_search_graph"
_QUERY_TOOL = "codebase-memory-mcp_query_graph"
# One-char typo (missing "a" in graph) — must stay below typos false-positive threshold.
_TYPO_GRAPH_TOOL = "codebase-memory-mcp_search_grph"


def test_fuzzy_resolve_returns_none_for_unrelated_query() -> None:
    assert (
        fuzzy_resolve_tool_name(
            [_GRAPH_TOOL],
            "totally-unrelated-xyz-tool-name",
        )
        is None
    )


def test_fuzzy_resolve_picks_highest_scoring_near_match() -> None:
    resolved = fuzzy_resolve_tool_name(
        [_GRAPH_TOOL, _QUERY_TOOL],
        _TYPO_GRAPH_TOOL,
    )
    assert resolved == _GRAPH_TOOL


def test_fuzzy_resolve_exact_name_in_list() -> None:
    resolved = fuzzy_resolve_tool_name([_GRAPH_TOOL], _GRAPH_TOOL)
    assert resolved == _GRAPH_TOOL

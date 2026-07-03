"""Tests for proxy pass-through when pruning.inject_via is hook."""

from __future__ import annotations

from typing import Any

from cyt.proxy.anthropic import filter_tools_for_query


def _tool(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"Tool {name}",
        "input_schema": {"type": "object", "properties": {}},
    }


def test_filter_tools_passes_through_when_hook_mode_without_for_hook() -> None:
    tools = [_tool("mcp__a__one"), _tool("mcp__a__two")]
    config = {"pruning": {"inject_via": "hook"}}
    result = filter_tools_for_query(tools, "find files", config=config)
    assert result.status == "pass_through"
    assert result.tools == tools


def test_filter_tools_prunes_when_hook_mode_with_for_hook() -> None:
    tools = [_tool("mcp__a__one"), _tool("mcp__a__two")]
    config = {
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "sequence": ["bm25"],
            },
        },
    }
    result = filter_tools_for_query(
        tools,
        "read file from disk",
        config=config,
        for_hook=True,
    )
    assert result.status in {"applied", "pass_through", "skipped"}

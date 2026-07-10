"""Tests for executor hook tool_kind override."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from cyt.indexer.policies import PolicyContext, effective_policy
from cyt.tools.policy_context import apply_executor_tool_kind


def test_executor_address_uses_system_policy_without_override() -> None:
    ctx = PolicyContext()
    tool_id = "tools.demo.org.default.search"
    assert effective_policy(tool_id, ctx) == "prune_optional"


def test_executor_address_uses_mcp_policy_with_tool_kind_override() -> None:
    ctx = PolicyContext()
    apply_executor_tool_kind(ctx, "mcp")
    tool_id = "tools.demo.org.default.search"
    assert effective_policy(tool_id, ctx) == "prune_all"


def test_filter_tools_for_query_applies_executor_tool_kind_for_hook() -> None:
    from cyt.proxy.anthropic import filter_tools_for_query

    config: dict[str, Any] = {
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "hook": {"tools_from": "executor"},
                "sequence": ["bm25"],
            },
        },
    }
    tools = [
        {
            "name": "tools.demo.org.default.search",
            "description": "Search",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]
    captured: list[PolicyContext] = []

    def capture_run_catalog_pruning(*args: object, **kwargs: object) -> tuple:
        captured.append(args[5])
        captured.append(args[6])
        raise RuntimeError("stop-after-context")

    with (
        patch(
            "cyt.pruners.tools_filter._run_catalog_pruning",
            side_effect=capture_run_catalog_pruning,
        ),
        patch("cyt.pruners.tools_filter.request_pass_through", return_value=False),
    ):
        try:
            filter_tools_for_query(
                tools,
                "search files",
                config=config,
                for_hook=True,
            )
        except RuntimeError as exc:
            assert str(exc) == "stop-after-context"

    assert len(captured) == 2
    assert captured[0].tool_kind == "mcp"
    assert captured[1].tool_kind == "mcp"

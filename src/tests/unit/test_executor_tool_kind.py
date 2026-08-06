"""Tests for executor hook tool_kind override."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

import pytest

from cyt.indexer.policies import PolicyContext, effective_policy
from cyt.testing.inject_via_maps import INJECT_VIA_ALL_PROXY
from cyt.tools.policy_context import apply_executor_tool_kind, prepare_hook_executor_tool_pruning

_HOOK_EXECUTOR_CONFIG: dict[str, Any] = {
    "pruning": {
        "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
        "tools": {
            "hook": {"tools_from": "executor"},
        },
    },
}


def test_executor_address_uses_system_policy_without_override() -> None:
    ctx = PolicyContext()
    tool_id = "tools.demo.org.default.search"
    assert effective_policy(tool_id, ctx) == "prune_optional"


def test_executor_address_uses_mcp_policy_with_tool_kind_override() -> None:
    ctx = PolicyContext()
    apply_executor_tool_kind(ctx, "mcp")
    tool_id = "tools.demo.org.default.search"
    assert effective_policy(tool_id, ctx) == "prune_all"


def test_prepare_hook_executor_tool_pruning_sets_tool_kind_on_all_contexts() -> None:
    scoring = PolicyContext()
    output = PolicyContext()
    mcp_cache = {"tools_list": [{"name": "execute"}], "execute_skill": "# execute"}

    with patch(
        "cyt.executor.http.get_executor_mcp_cache",
        return_value=mcp_cache,
    ) as cache_mock:
        loaded = prepare_hook_executor_tool_pruning(
            _HOOK_EXECUTOR_CONFIG,
            scoring,
            output,
        )

    assert loaded == mcp_cache
    cache_mock.assert_called_once()
    assert scoring.tool_kind == "mcp"
    assert output.tool_kind == "mcp"


def test_prepare_hook_executor_tool_pruning_noop_in_proxy_mode() -> None:
    ctx = PolicyContext()
    config: dict[str, Any] = {
        "pruning": {
            "inject_via": dict(INJECT_VIA_ALL_PROXY),
            "tools": {"hook": {"tools_from": "executor"}},
        },
    }
    with patch(
        "cyt.executor.http.get_executor_mcp_cache",
        side_effect=AssertionError("must not warm MCP cache in proxy mode"),
    ):
        assert prepare_hook_executor_tool_pruning(config, ctx) is None
    assert ctx.tool_kind is None


def _capture_filter_tools_contexts(
    config: dict[str, Any],
    *,
    sequence: list[str],
) -> list[PolicyContext]:
    from cyt.proxy.anthropic import filter_tools_for_query

    config = {
        **config,
        "pruning": {
            **config["pruning"],
            "tools": {
                **config["pruning"]["tools"],
                "sequence": sequence,
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
        captured.append(cast(PolicyContext, args[5]))
        captured.append(cast(PolicyContext, args[6]))
        raise RuntimeError("stop-after-context")

    with (
        patch(
            "cyt.pruners.tools_filter._run_catalog_pruning",
            side_effect=capture_run_catalog_pruning,
        ),
        patch("cyt.pruners.tools_filter.request_pass_through", return_value=False),
        patch(
            "cyt.executor.http.get_executor_mcp_cache",
            return_value=None,
        ),
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

    return captured


@pytest.mark.parametrize("sequence", [["bm25"], ["rerank"], ["llm"], ["bm25", "rerank", "llm"]])
def test_filter_tools_for_query_applies_executor_tool_kind_for_hook(sequence: list[str]) -> None:
    captured = _capture_filter_tools_contexts(_HOOK_EXECUTOR_CONFIG, sequence=sequence)
    assert len(captured) == 2
    assert captured[0].tool_kind == "mcp"
    assert captured[1].tool_kind == "mcp"


def test_filter_tools_for_query_warms_mcp_cache_on_hook_executor() -> None:
    from cyt.proxy.anthropic import filter_tools_for_query

    config: dict[str, Any] = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
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

    with (
        patch(
            "cyt.pruners.tools_filter._run_catalog_pruning",
            side_effect=RuntimeError("stop-after-context"),
        ),
        patch("cyt.pruners.tools_filter.request_pass_through", return_value=False),
        patch(
            "cyt.executor.http.get_executor_mcp_cache",
            return_value={"tools_list": [], "execute_skill": ""},
        ) as cache_mock,
    ):
        try:
            filter_tools_for_query(
                tools,
                "search files",
                config=config,
                for_hook=True,
            )
        except RuntimeError:
            pass

    assert cache_mock.call_count >= 1

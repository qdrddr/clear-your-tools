"""Proxy mode must not touch Executor (API, disk cache, or MCP cache)."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

from cyt.proxy.anthropic import transform_anthropic_request
from cyt.pruners.llm import tool_selector_system_prompt
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.tools.sources.executor_http import (
    executor_catalog_health_snapshot,
    get_executor_catalog,
    get_executor_mcp_cache,
    schedule_executor_catalog_refresh,
)

_PROXY_CONFIG: dict[str, Any] = {
    "pruning": {
        "inject_via": "proxy",
        "tools": {
            "enabled": True,
            "hook": {
                "tools_from": "executor",
                "executor_url": "http://localhost:4789",
                "executor_token_var": "EXECUTOR_TOKEN",
            },
        },
    },
}


def _tool(name: str, *, description: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "description": description or f"Tool {name}",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query"},
            },
        },
    }


def _proxy_config(*, sequence: list[str]) -> dict[str, Any]:
    import copy

    config = copy.deepcopy(_PROXY_CONFIG)
    config["pruning"]["tools"]["sequence"] = sequence
    return config


def _executor_forbidden(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("executor must not be called when inject_via is proxy")


@pytest.fixture
def forbid_executor_runtime() -> Generator[None]:
    with (
        patch(
            "cyt.tools.sources.executor_http._load_catalog_from_disk",
            side_effect=_executor_forbidden,
        ),
        patch(
            "cyt.tools.sources.executor_http._blocking_network_fetch",
            side_effect=_executor_forbidden,
        ),
        patch(
            "cyt.tools.sources.executor_http._start_background_refresh",
            side_effect=_executor_forbidden,
        ),
    ):
        yield


def test_get_executor_mcp_cache_returns_none_in_proxy_mode() -> None:
    assert get_executor_mcp_cache(_PROXY_CONFIG, allow_prompt=False) is None


def test_get_executor_catalog_returns_none_in_proxy_mode() -> None:
    assert get_executor_catalog(_PROXY_CONFIG, allow_prompt=False) is None


def test_schedule_executor_catalog_refresh_noops_in_proxy_mode() -> None:
    with patch(
        "cyt.tools.sources.executor_http._start_background_refresh",
        side_effect=_executor_forbidden,
    ):
        schedule_executor_catalog_refresh(_PROXY_CONFIG, force=True)


def test_executor_catalog_health_snapshot_empty_in_proxy_mode() -> None:
    assert executor_catalog_health_snapshot(_PROXY_CONFIG) == {}


def test_tool_selector_system_prompt_skips_executor_appendix_in_proxy_mode() -> None:
    with patch(
        "cyt.tools.sources.executor_http.get_executor_mcp_cache",
        side_effect=_executor_forbidden,
    ):
        prompt = tool_selector_system_prompt(_PROXY_CONFIG)
    assert '<skill name="executor"' not in prompt
    assert "Executor MCP transport context" not in prompt
    assert "These are MCP tools and their enums" in prompt


@pytest.mark.parametrize("sequence", [["bm25"], ["rerank"], ["llm"]])
def test_proxy_pipeline_does_not_touch_executor(
    sequence: list[str],
    forbid_executor_runtime: None,
) -> None:
    tools = [
        _tool("mcp__alpha__search", description="search files on disk"),
        _tool("mcp__beta__read", description="read file contents"),
        _tool("mcp__gamma__write", description="write file contents"),
    ]
    config = _proxy_config(sequence=sequence)
    llm_patches: list[Any] = []
    if sequence == ["llm"]:
        from cyt.pruners.llm import ChunkSelection, RelevantChunkSelections

        fake_response = RelevantChunkSelections(selections=[ChunkSelection(id=0, score=90)])
        llm_patches.append(
            patch(
                "cyt.pruners.llm.call_llm",
                return_value=(fake_response, None),
            ),
        )
    if sequence == ["rerank"]:
        llm_patches.append(
            patch(
                "cyt.pruners.rerank.rerank",
                return_value={"results": [{"index": 0, "relevance_score": 0.9}]},
            ),
        )

    with patch.multiple(
        "cyt.tools.sources.executor_http",
        get_executor_mcp_cache=_executor_forbidden,
        get_executor_catalog=_executor_forbidden,
        schedule_executor_catalog_refresh=_executor_forbidden,
        load_executor_catalog_from_disk=_executor_forbidden,
    ):
        for llm_patch in llm_patches:
            llm_patch.start()
        try:
            result = filter_tools_for_query(
                tools,
                "search and read files from disk",
                config=config,
                for_hook=False,
            )
        finally:
            for llm_patch in llm_patches:
                llm_patch.stop()

    assert result.status in {"applied", "pass_through", "skipped", "failed"}


def test_proxy_anthropic_transform_without_executor_credentials(
    forbid_executor_runtime: None,
) -> None:
    config = _proxy_config(sequence=["bm25"])
    config["pruning"]["tools"]["hook"]["executor_url"] = ""
    body = {
        "model": "claude-test",
        "messages": [{"role": "user", "content": "find files on disk"}],
        "tools": [
            _tool("mcp__alpha__search", description="search files"),
            _tool("mcp__beta__read", description="read files"),
        ],
    }
    with patch.multiple(
        "cyt.tools.sources.executor_http",
        get_executor_mcp_cache=_executor_forbidden,
        get_executor_catalog=_executor_forbidden,
        schedule_executor_catalog_refresh=_executor_forbidden,
        load_executor_catalog_from_disk=_executor_forbidden,
    ):
        out, prune_result, _skills_meta = transform_anthropic_request(body, config=config)

    assert prune_result is not None
    assert out["messages"]


def test_filter_tools_for_query_skips_mcp_cache_warm_in_proxy_mode() -> None:
    tools = [
        _tool("mcp__alpha__search", description="search files on disk"),
    ]
    config = _proxy_config(sequence=["bm25"])

    with (
        patch(
            "cyt.pruners.tools_filter._run_catalog_pruning",
            side_effect=RuntimeError("stop-after-context"),
        ),
        patch("cyt.pruners.tools_filter.request_pass_through", return_value=False),
        patch(
            "cyt.tools.sources.executor_http.get_executor_mcp_cache",
            side_effect=_executor_forbidden,
        ),
    ):
        try:
            filter_tools_for_query(
                tools,
                "search files",
                config=config,
                for_hook=False,
            )
        except RuntimeError:
            pass

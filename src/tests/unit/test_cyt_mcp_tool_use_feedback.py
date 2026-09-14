"""Tests for cyt-mcp tool use feedback middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams, TextContent

from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.tool_use_feedback_middleware import (
    ToolUseFeedbackMiddleware,
    _is_meta_tool,
    _tool_call_succeeded,
)


def test_is_meta_tool_skips_get_tool_definitions() -> None:
    assert _is_meta_tool("get-tool-definitions") is True
    assert _is_meta_tool("codebase-memory_search_graph") is False


def test_tool_call_succeeded_respects_is_error() -> None:
    ok = ToolResult(content=[TextContent(type="text", text="ok")], is_error=False)
    err = ToolResult(content=[TextContent(type="text", text="fail")], is_error=True)
    assert _tool_call_succeeded(ok) is True
    assert _tool_call_succeeded(err) is False


@pytest.mark.asyncio
async def test_middleware_reports_success_and_failure() -> None:
    config = sample_aggregator_config(catalog_scope="workspace")
    cache = RuntimeToolCache()
    cache.replace(
        [{"name": "srv_tool", "inputSchema": {"type": "object"}}],
        search_index={"srv_tool": {"name": "srv_tool", "inputSchema": {"type": "object"}}},
    )
    holder = ConfigHolder(config)
    middleware = ToolUseFeedbackMiddleware(MagicMock(), cache, holder, config=config)

    scheduled: list[dict] = []

    def capture_schedule(**kwargs: object) -> None:
        scheduled.append(kwargs)

    params = CallToolRequestParams(name="srv_tool", arguments={"q": "x"})
    context = MagicMock()
    context.message = params

    ok_result = ToolResult(content=[TextContent(type="text", text="ok")])
    call_next = AsyncMock(return_value=ok_result)

    with patch(
        "cyt_mcp.tool_use_feedback_middleware.schedule_tool_use_feedback",
        side_effect=capture_schedule,
    ):
        result = await middleware.on_call_tool(context, call_next)
    assert result is ok_result
    assert scheduled[0]["success"] is True
    assert scheduled[0]["tool_name"] == "srv_tool"

    scheduled.clear()
    call_next = AsyncMock(
        return_value=ToolResult(
            content=[TextContent(type="text", text="err")],
            is_error=True,
        ),
    )
    with patch(
        "cyt_mcp.tool_use_feedback_middleware.schedule_tool_use_feedback",
        side_effect=capture_schedule,
    ):
        await middleware.on_call_tool(context, call_next)
    assert scheduled[0]["success"] is False


@pytest.mark.asyncio
async def test_middleware_reports_failure_when_call_next_raises() -> None:
    config = sample_aggregator_config(catalog_scope="workspace")
    cache = RuntimeToolCache()
    holder = ConfigHolder(config)
    middleware = ToolUseFeedbackMiddleware(MagicMock(), cache, holder, config=config)

    scheduled: list[dict] = []

    params = CallToolRequestParams(name="srv_tool", arguments={"q": "x"})
    context = MagicMock()
    context.message = params

    call_next = AsyncMock(side_effect=RuntimeError("backend down"))

    with (
        patch(
            "cyt_mcp.tool_use_feedback_middleware.schedule_tool_use_feedback",
            side_effect=lambda **kwargs: scheduled.append(kwargs),
        ),
        pytest.raises(RuntimeError, match="backend down"),
    ):
        await middleware.on_call_tool(context, call_next)

    assert scheduled[0]["success"] is False
    assert scheduled[0]["tool_name"] == "srv_tool"

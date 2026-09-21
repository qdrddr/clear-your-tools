"""Tests for cyt-mcp tools/list_changed notification middleware."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.middleware import MiddlewareContext

from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.tool_list_notify import ToolListChangedMiddleware, notify_all_sessions_list_changed


def _test_config_holder() -> ConfigHolder:
    return ConfigHolder(sample_aggregator_config())


def _initialize_context(*, session_id: str = "sess-1") -> MiddlewareContext[Any]:
    session = MagicMock()
    session.send_tool_list_changed = AsyncMock()
    fastmcp_ctx = MagicMock()
    fastmcp_ctx.session = session
    fastmcp_ctx.session_id = session_id
    return MiddlewareContext(
        message=MagicMock(),
        source="client",
        type="request",
        method="initialize",
        fastmcp_context=fastmcp_ctx,
    )


@pytest.mark.asyncio
async def test_notify_after_initialize_waits_for_stable_catalog() -> None:
    server = MagicMock()
    cache = RuntimeToolCache()
    middleware = ToolListChangedMiddleware(
        server,
        cache,
        _test_config_holder(),
        notify_attempts=4,
        notify_delay_s=0,
    )
    context = _initialize_context()

    async def _wait_ready(active_cache: RuntimeToolCache, **_kwargs: object) -> int:
        count = len(active_cache.snapshot())
        if count == 0:
            active_cache.replace([{"name": "alpha_tool", "inputSchema": {"type": "object"}}])
            return 1
        active_cache.replace(
            [
                {"name": "alpha_tool", "inputSchema": {"type": "object"}},
                {"name": "beta_tool", "inputSchema": {"type": "object"}},
            ],
        )
        return 2

    with patch(
        "cyt_mcp.catalog_build.wait_for_catalog_cache_ready",
        side_effect=_wait_ready,
    ):
        result = await middleware.on_initialize(context, AsyncMock(return_value=None))
        assert result is None
        await asyncio.sleep(0)

    fastmcp_ctx = context.fastmcp_context
    assert fastmcp_ctx is not None
    session = fastmcp_ctx.session
    assert isinstance(session.send_tool_list_changed, AsyncMock)
    session.send_tool_list_changed.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_dedupes_per_session() -> None:
    server = MagicMock()
    cache = RuntimeToolCache()
    cache.replace([{"name": "only_tool", "inputSchema": {"type": "object"}}])
    middleware = ToolListChangedMiddleware(
        server,
        cache,
        _test_config_holder(),
        notify_attempts=1,
        notify_delay_s=0,
    )
    context = _initialize_context()

    with patch(
        "cyt_mcp.catalog_build.wait_for_catalog_cache_ready",
        new_callable=AsyncMock,
        return_value=1,
    ):
        await middleware.on_initialize(context, AsyncMock(return_value=None))
        await middleware.on_initialize(context, AsyncMock(return_value=None))
        await asyncio.sleep(0)

    fastmcp_ctx = context.fastmcp_context
    assert fastmcp_ctx is not None
    assert isinstance(fastmcp_ctx.session.send_tool_list_changed, AsyncMock)
    fastmcp_ctx.session.send_tool_list_changed.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_all_sessions_sends_list_changed() -> None:
    server = MagicMock()
    cache = RuntimeToolCache()
    cache.replace([{"name": "alpha_tool", "inputSchema": {"type": "object"}}])
    middleware = ToolListChangedMiddleware(server, cache, _test_config_holder())
    session = MagicMock()
    session.send_tool_list_changed = AsyncMock()
    middleware._sessions["sess"] = session
    await middleware.notify_all_sessions()
    session.send_tool_list_changed.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_all_sessions_list_changed_delegates() -> None:
    middleware = MagicMock()
    middleware.notify_all_sessions = AsyncMock()
    await notify_all_sessions_list_changed(middleware)
    middleware.notify_all_sessions.assert_awaited_once()

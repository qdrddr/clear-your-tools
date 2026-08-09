"""Tests for cyt-mcp tools/list_changed notification middleware."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.middleware import MiddlewareContext

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.tool_list_notify import ToolListChangedMiddleware


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
        notify_attempts=4,
        notify_delay_s=0,
    )
    context = _initialize_context()

    async def _refresh(_server: object, active_cache: RuntimeToolCache) -> None:
        count = len(active_cache.snapshot())
        if count == 0:
            active_cache.replace([{"name": "alpha_tool", "inputSchema": {"type": "object"}}])
        else:
            active_cache.replace(
                [
                    {"name": "alpha_tool", "inputSchema": {"type": "object"}},
                    {"name": "beta_tool", "inputSchema": {"type": "object"}},
                ],
            )

    with patch(
        "cyt_mcp.tool_list_notify.refresh_catalog_cache",
        side_effect=_refresh,
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
        notify_attempts=1,
        notify_delay_s=0,
    )
    context = _initialize_context()

    with patch(
        "cyt_mcp.tool_list_notify.refresh_catalog_cache",
        new_callable=AsyncMock,
    ):
        await middleware.on_initialize(context, AsyncMock(return_value=None))
        await middleware.on_initialize(context, AsyncMock(return_value=None))
        await asyncio.sleep(0)

    fastmcp_ctx = context.fastmcp_context
    assert fastmcp_ctx is not None
    assert isinstance(fastmcp_ctx.session.send_tool_list_changed, AsyncMock)
    fastmcp_ctx.session.send_tool_list_changed.assert_awaited_once()

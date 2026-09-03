"""Send MCP ``notifications/tools/list_changed`` after session (re)start.

Cursor reload spawns a new cyt-mcp process and may call ``tools/list`` before
backend proxy subprocesses are ready. FastMCP already advertises
``tools.listChanged``; this middleware refreshes the runtime catalog once the
session is live and notifies the client to re-fetch the tool list.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.server.session import ServerSession
from mcp.types import InitializeRequest, InitializeResult

from cyt_mcp.catalog_build import refresh_catalog_cache
from cyt_mcp.config import AggregatorConfig
from cyt_mcp.runtime_cache import RuntimeToolCache

logger = logging.getLogger(__name__)

DEFAULT_NOTIFY_ATTEMPTS = 12
DEFAULT_NOTIFY_DELAY_S = 0.25


class ToolListChangedMiddleware(Middleware):
    """Refresh backend catalog after initialize and notify clients to re-list tools."""

    def __init__(
        self,
        server: FastMCP,
        cache: RuntimeToolCache,
        config: AggregatorConfig,
        *,
        notify_attempts: int = DEFAULT_NOTIFY_ATTEMPTS,
        notify_delay_s: float = DEFAULT_NOTIFY_DELAY_S,
    ) -> None:
        self._server = server
        self._cache = cache
        self._config = config
        self._notify_attempts = max(1, notify_attempts)
        self._notify_delay_s = max(0.0, notify_delay_s)
        self._pending: set[str] = set()
        self._notify_tasks: set[asyncio.Task[None]] = set()

    async def on_initialize(
        self,
        context: MiddlewareContext[InitializeRequest],
        call_next: CallNext[InitializeRequest, InitializeResult | None],
    ) -> InitializeResult | None:
        result = await call_next(context)
        self._schedule_notify(context)
        return result

    def _schedule_notify(self, context: MiddlewareContext[Any]) -> None:
        fastmcp_ctx = context.fastmcp_context
        if fastmcp_ctx is None or fastmcp_ctx.session is None:
            return
        session_key = fastmcp_ctx.session_id or str(id(fastmcp_ctx.session))
        if session_key in self._pending:
            return
        self._pending.add(session_key)
        task = asyncio.create_task(
            self._notify_when_ready(fastmcp_ctx.session, session_key=session_key),
            name="cyt-mcp-tool-list-changed",
        )
        self._notify_tasks.add(task)
        task.add_done_callback(self._notify_tasks.discard)

    async def _notify_when_ready(self, session: ServerSession, *, session_key: str) -> None:
        try:
            previous_count = -1
            for attempt in range(self._notify_attempts):
                try:
                    await refresh_catalog_cache(self._server, self._cache, self._config)
                except Exception as exc:
                    logger.debug(
                        "cyt-mcp: catalog refresh before list_changed failed: %s",
                        exc,
                    )
                tool_count = len(self._cache.snapshot())
                stable = tool_count > 0 and tool_count == previous_count
                previous_count = tool_count
                if stable or attempt == self._notify_attempts - 1:
                    await session.send_tool_list_changed()
                    logger.info(
                        "cyt-mcp: sent notifications/tools/list_changed tool_count=%d attempt=%d",
                        tool_count,
                        attempt + 1,
                    )
                    return
                if self._notify_delay_s:
                    await asyncio.sleep(self._notify_delay_s)
        except Exception as exc:
            logger.warning("cyt-mcp: failed to send tools/list_changed: %s", exc)
        finally:
            self._pending.discard(session_key)


def register_tool_list_changed_middleware(
    server: FastMCP,
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> None:
    server.add_middleware(ToolListChangedMiddleware(server, cache, config))

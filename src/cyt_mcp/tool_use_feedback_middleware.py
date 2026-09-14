"""Report cyt-mcp tool calls to hook daemon for tier statistics."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams

from cyt.tool_examples.identity import resolve_mcp_server_and_tool_from_wire_name
from cyt_mcp.catalog import catalog_tools_content_hash
from cyt_mcp.config import AggregatorConfig
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME, SEARCH_TOOL_NAME
from cyt_mcp.tier_feedback_push import schedule_tool_use_feedback

logger = logging.getLogger(__name__)

_META_TOOL_NAMES = frozenset(
    {
        SEARCH_TOOL_NAME,
        MCP_WIRE_SEARCH_TOOL_NAME,
        "cyt-mcp_get-tool-definitions",
    },
)


def _is_meta_tool(tool_name: str) -> bool:
    normalized = tool_name.strip()
    if not normalized:
        return True
    if normalized in _META_TOOL_NAMES:
        return True
    return normalized.endswith(("_get-tool-definitions", "__get-tool-definitions"))


def _tool_call_succeeded(result: ToolResult) -> bool:
    if getattr(result, "is_error", False):
        return False
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and structured.get("isError") is True:
        return False
    return True


def _input_schema_for_tool(
    cache: RuntimeToolCache,
    tool_name: str,
) -> dict[str, Any] | None:
    entry = cache.search_index_entry(tool_name)
    if entry is None:
        return None
    schema = entry.get("inputSchema")
    return dict(schema) if isinstance(schema, dict) else None


class ToolUseFeedbackMiddleware(Middleware):
    """Capture tool call success/failure and report to hook daemon asynchronously."""

    def __init__(
        self,
        server: FastMCP,
        cache: RuntimeToolCache,
        config_holder: ConfigHolder,
        *,
        config: AggregatorConfig,
    ) -> None:
        self._server = server
        self._cache = cache
        self._config_holder = config_holder
        self._config = config

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        params = context.message
        tool_name = str(getattr(params, "name", "") or "").strip()
        arguments = getattr(params, "arguments", None)
        args = dict(arguments) if isinstance(arguments, dict) else {}

        if _is_meta_tool(tool_name):
            return await call_next(context)

        success = False
        try:
            result = await call_next(context)
            success = _tool_call_succeeded(result)
        except Exception:
            success = False
            raise
        finally:
            if tool_name:
                mcp_server, bare_tool = resolve_mcp_server_and_tool_from_wire_name(tool_name)
                input_schema = _input_schema_for_tool(self._cache, tool_name)
                catalog_hash = catalog_tools_content_hash(self._cache.snapshot())
                schedule_tool_use_feedback(
                    config=self._config,
                    tool_name=tool_name,
                    args=args,
                    success=success,
                    catalog_content_hash=catalog_hash,
                    mcp_server=mcp_server,
                    bare_tool_name=bare_tool,
                    input_schema=input_schema if success else None,
                    server=self._server,
                    cache=self._cache,
                    config_holder=self._config_holder,
                )

        return result


def register_tool_use_feedback_middleware(
    server: FastMCP,
    cache: RuntimeToolCache,
    config_holder: ConfigHolder,
    *,
    config: AggregatorConfig,
) -> ToolUseFeedbackMiddleware:
    middleware = ToolUseFeedbackMiddleware(
        server,
        cache,
        config_holder,
        config=config,
    )
    server.add_middleware(middleware)
    return middleware

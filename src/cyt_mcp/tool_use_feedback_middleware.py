"""Report cyt-mcp tool calls to hook daemon for tier statistics."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams

from cyt_mcp.catalog import catalog_tools_content_hash
from cyt_mcp.config import AggregatorConfig, load_known_mcp_server_keys
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.session_context import get_current_session_runtime
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME, SEARCH_TOOL_NAME
from cyt_mcp.tier_feedback_push import schedule_tool_use_feedback
from cyt_mcp.tool_identity import canonical_backend_identity, resolve_backend_identity

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


def _resolve_tool_identity(
    cache: RuntimeToolCache,
    tool_name: str,
    *,
    project_root: str | None = None,
) -> tuple[str, str]:
    server_keys = load_known_mcp_server_keys(
        project_root=project_root,
    )
    for entry in cache.snapshot():
        if str(entry.get("name") or "") != tool_name:
            continue
        if server_keys:
            return canonical_backend_identity(entry, server_keys)
        return resolve_backend_identity(entry)
    if server_keys:
        return canonical_backend_identity({"name": tool_name}, server_keys)
    return "unknown", tool_name.strip() or "unknown"


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
                runtime = get_current_session_runtime()
                active_config = runtime.config if runtime is not None else self._config
                active_cache = runtime.cache if runtime is not None else self._cache
                active_holder = runtime.config_holder if runtime is not None else self._config_holder
                project_root = (
                    str(active_config.workspace_root.expanduser().resolve())
                    if active_config.workspace_root is not None
                    else None
                )
                mcp_server, bare_tool = _resolve_tool_identity(
                    active_cache,
                    tool_name,
                    project_root=project_root,
                )
                input_schema = _input_schema_for_tool(active_cache, tool_name)
                catalog_hash = catalog_tools_content_hash(active_cache.snapshot())
                schedule_tool_use_feedback(
                    config=active_config,
                    tool_name=tool_name,
                    args=args,
                    success=success,
                    catalog_content_hash=catalog_hash,
                    mcp_server=mcp_server,
                    bare_tool_name=bare_tool,
                    input_schema=input_schema if success else None,
                    server=self._server,
                    cache=active_cache,
                    config_holder=active_holder,
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

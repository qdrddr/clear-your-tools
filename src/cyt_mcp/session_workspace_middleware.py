"""Bind MCP sessions to workspace-specific tool catalogs."""

from __future__ import annotations

import contextvars
from collections.abc import Sequence
from typing import Any, cast

# cast used when reading session off FastMCP request context (untyped upstream)
from fastmcp import FastMCP
from fastmcp.prompts.base import PromptResult
from fastmcp.resources.base import ResourceResult
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult
from mcp.server.session import ServerSession
from mcp.types import (
    CallToolRequestParams,
    GetPromptRequestParams,
    ListPromptsRequest,
    ListResourcesRequest,
    ListResourceTemplatesRequest,
    ListToolsRequest,
    ReadResourceRequestParams,
)

from cyt_mcp.session_context import (
    reset_current_session_runtime,
    reset_session_scoping_active,
    set_current_session_runtime,
    set_session_scoping_active,
)
from cyt_mcp.session_runtime import MultiWorkspaceCoordinator, WorkspaceSessionRuntime
from cyt_mcp.stubs import list_stubs_from_runtime_cache


def _mcp_session(fastmcp_ctx: object) -> ServerSession | None:
    """Return the live MCP session when established; None for CLI/catalog calls."""
    if fastmcp_ctx is None:
        return None
    request_ctx = getattr(fastmcp_ctx, "request_context", None)
    if request_ctx is None:
        return None
    session = getattr(request_ctx, "session", None)
    if session is None:
        return None
    return cast(ServerSession, session)


class SessionWorkspaceMiddleware(Middleware):
    """Resolve workspace per MCP session and scope tools/list + tool calls."""

    _WORKSPACE_METHODS = frozenset({"tools/list", "tools/call"})

    def __init__(self, coordinator: MultiWorkspaceCoordinator) -> None:
        self._coordinator = coordinator

    async def _bind_request_session(
        self,
        context: MiddlewareContext[Any],
    ) -> contextvars.Token[Any] | None:
        session_key = self._session_key(context)
        fastmcp_ctx = context.fastmcp_context
        session = _mcp_session(fastmcp_ctx)
        if session_key is None:
            return None
        if session is None:
            return None
        runtime = await self._coordinator.bind_session(
            session,
            session_key=session_key,
        )
        return set_current_session_runtime(runtime)

    def _session_key(self, context: MiddlewareContext[Any]) -> str | None:
        fastmcp_ctx = context.fastmcp_context
        if fastmcp_ctx is None:
            return None
        session = _mcp_session(fastmcp_ctx)
        if session is None:
            return None
        return fastmcp_ctx.session_id or str(id(session))

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> object:
        method = str(getattr(context, "method", "") or "")
        token = None
        scoping_token = None
        if method in self._WORKSPACE_METHODS:
            token = await self._bind_request_session(context)
            if token is not None:
                scoping_token = set_session_scoping_active(True)
        try:
            return await call_next(context)
        finally:
            if scoping_token is not None:
                reset_session_scoping_active(scoping_token)
            if token is not None:
                reset_current_session_runtime(token)

    async def _resolve_runtime(
        self,
        context: MiddlewareContext[Any],
    ) -> WorkspaceSessionRuntime:
        runtime = self._coordinator.bootstrap
        session_key = self._session_key(context)
        session = _mcp_session(context.fastmcp_context)
        if session_key is not None:
            if session is not None:
                runtime = await self._coordinator.bind_session(
                    session,
                    session_key=session_key,
                )
        return runtime

    async def _proxy_offering_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> object:
        runtime = await self._resolve_runtime(context)
        self._coordinator.ensure_backends_mounted(runtime.config.mcp_servers)
        return await call_next(context)

    async def on_list_prompts(
        self,
        context: MiddlewareContext[ListPromptsRequest],
        call_next: CallNext[ListPromptsRequest, Sequence[Any]],
    ) -> Sequence[Any]:
        return cast(Sequence[Any], await self._proxy_offering_request(context, call_next))

    async def on_list_resources(
        self,
        context: MiddlewareContext[ListResourcesRequest],
        call_next: CallNext[ListResourcesRequest, Sequence[Any]],
    ) -> Sequence[Any]:
        return cast(Sequence[Any], await self._proxy_offering_request(context, call_next))

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[ListResourceTemplatesRequest],
        call_next: CallNext[ListResourceTemplatesRequest, Sequence[Any]],
    ) -> Sequence[Any]:
        return cast(Sequence[Any], await self._proxy_offering_request(context, call_next))

    async def on_read_resource(
        self,
        context: MiddlewareContext[ReadResourceRequestParams],
        call_next: CallNext[ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        return cast(ResourceResult, await self._proxy_offering_request(context, call_next))

    async def on_get_prompt(
        self,
        context: MiddlewareContext[GetPromptRequestParams],
        call_next: CallNext[GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        return cast(PromptResult, await self._proxy_offering_request(context, call_next))

    async def on_list_tools(
        self,
        context: MiddlewareContext[ListToolsRequest],
        call_next: CallNext[ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        from cyt_mcp.catalog_build import hydrate_runtime_cache, wait_for_catalog_cache_ready

        session_key = self._session_key(context)
        fastmcp_ctx = context.fastmcp_context
        runtime = self._coordinator.bootstrap
        session = _mcp_session(fastmcp_ctx)
        if session_key is not None:
            if session is not None:
                runtime = await self._coordinator.bind_session(
                    session,
                    session_key=session_key,
                )
        token = set_current_session_runtime(runtime)
        scoping_token = set_session_scoping_active(True)
        try:
            _cache_count = len(runtime.cache.snapshot())
            if _cache_count == 0:
                hydrate_runtime_cache(runtime.cache, runtime.config)
                _cache_count = len(runtime.cache.snapshot())
            if _cache_count == 0:
                _cache_count = await wait_for_catalog_cache_ready(
                    runtime.cache,
                    max_wait_seconds=8.0,
                )
            if _cache_count == 0:
                hydrate_runtime_cache(runtime.cache, runtime.config)
                _cache_count = len(runtime.cache.snapshot())
            if _cache_count == 0 and runtime is not self._coordinator.bootstrap:
                bootstrap = self._coordinator.bootstrap
                bootstrap_count = len(bootstrap.cache.snapshot())
                if bootstrap_count > 0:
                    runtime = bootstrap
                    _cache_count = bootstrap_count
            if _cache_count:
                return await list_stubs_from_runtime_cache(
                    runtime.cache,
                    runtime.config,
                    config_holder=runtime.config_holder,
                )
            self._coordinator.ensure_backends_mounted(runtime.config.mcp_servers)
            return await call_next(context)
        finally:
            reset_session_scoping_active(scoping_token)
            reset_current_session_runtime(token)

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        session_key = self._session_key(context)
        fastmcp_ctx = context.fastmcp_context
        runtime = self._coordinator.bootstrap
        session = _mcp_session(fastmcp_ctx)
        if session_key is not None:
            if session is not None:
                runtime = await self._coordinator.bind_session(
                    session,
                    session_key=session_key,
                )
        token = set_current_session_runtime(runtime)
        scoping_token = set_session_scoping_active(True)
        try:
            self._coordinator.ensure_backends_mounted(runtime.config.mcp_servers)
            return await call_next(context)
        finally:
            reset_session_scoping_active(scoping_token)
            reset_current_session_runtime(token)


def register_session_workspace_middleware(
    server: FastMCP,
    coordinator: MultiWorkspaceCoordinator,
) -> SessionWorkspaceMiddleware:
    middleware = SessionWorkspaceMiddleware(coordinator)
    server.add_middleware(middleware)
    return middleware

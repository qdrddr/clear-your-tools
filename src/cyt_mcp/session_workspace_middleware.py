"""Bind MCP sessions to workspace-specific tool catalogs."""

from __future__ import annotations

import contextvars
import logging
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
    InitializeRequest,
    InitializeResult,
    ListPromptsRequest,
    ListResourcesRequest,
    ListResourceTemplatesRequest,
    ListToolsRequest,
    Prompt,
    ReadResourceRequestParams,
    Resource,
    ResourceTemplate,
)

from cyt_mcp.offerings_cache import (
    enter_tools_list,
    exit_tools_list,
    offerings_to_wire,
)
from cyt_mcp.session_context import (
    reset_current_session_runtime,
    reset_session_scoping_active,
    set_current_session_runtime,
    set_session_scoping_active,
)
from cyt_mcp.session_runtime import MultiWorkspaceCoordinator, WorkspaceSessionRuntime
from cyt_mcp.stubs import list_stubs_from_runtime_cache

logger = logging.getLogger(__name__)


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

    def _offerings_key(self, runtime: WorkspaceSessionRuntime) -> str:
        from cyt_mcp.catalog_build import offerings_runtime_key

        return offerings_runtime_key(runtime.config) or "bootstrap"

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

    async def on_initialize(
        self,
        context: MiddlewareContext[InitializeRequest],
        call_next: CallNext[InitializeRequest, InitializeResult | None],
    ) -> InitializeResult | None:
        result = await call_next(context)
        runtime = await self._resolve_runtime(context)
        offerings_key = self._offerings_key(runtime)
        from cyt_mcp.catalog_build import hydrate_offerings_cache

        hydrate_offerings_cache(
            self._coordinator.offerings_cache,
            runtime.config,
            runtime_key=offerings_key,
        )
        if self._coordinator.offerings_cache.get(offerings_key) is None:
            self._schedule_offerings_refresh(runtime, offerings_key, delay_s=0.0)
        return result

    def _schedule_offerings_refresh(
        self,
        runtime: WorkspaceSessionRuntime,
        offerings_key: str,
        *,
        delay_s: float = 0.0,
    ) -> None:
        self._coordinator.offerings_cache.schedule_refresh_once(
            runtime_key=offerings_key,
            delay_s=delay_s,
            coro_factory=lambda: self._refresh_offerings_cache(runtime, offerings_key),
        )

    async def _notify_offerings_changed(self, runtime: WorkspaceSessionRuntime) -> None:
        from cyt_mcp.hook_daemon_push import _instance_key, _push_contexts

        ctx_key = _instance_key(runtime.config)
        ctx = _push_contexts.get(ctx_key)
        if ctx is None or ctx.list_changed_middleware is None:
            return
        await ctx.list_changed_middleware.notify_all_sessions_offerings_changed()

    async def _refresh_offerings_cache(
        self,
        runtime: WorkspaceSessionRuntime,
        offerings_key: str,
    ) -> None:
        from cyt_mcp.catalog_build import disk_catalog_slug_for_config

        before = self._coordinator.offerings_cache.snapshot_or_empty(offerings_key)
        try:
            snapshot = await self._coordinator.offerings_cache.refresh_from_server(
                self._coordinator.server,
                runtime_key=offerings_key,
                mcp_servers=runtime.config.mcp_servers,
                ensure_mounted=self._coordinator.ensure_backends_mounted,
                disk_slug=disk_catalog_slug_for_config(runtime.config),
            )
        except Exception as exc:
            logger.warning("cyt-mcp offerings refresh failed: %s", exc)
            return
        if snapshot.total_count > 0 and snapshot.total_count != before.total_count:
            await self._notify_offerings_changed(runtime)

    async def _cached_offerings(
        self,
        context: MiddlewareContext[Any],
        *,
        kind: str,
        wire_type: type[Any],
    ) -> Sequence[Any]:
        runtime = await self._resolve_runtime(context)
        offerings_key = self._offerings_key(runtime)
        snapshot = self._coordinator.offerings_cache.snapshot_or_empty(offerings_key)
        if snapshot.total_count == 0:
            self._schedule_offerings_refresh(runtime, offerings_key, delay_s=0.0)
        items = {
            "resources": snapshot.resources,
            "prompts": snapshot.prompts,
            "resource_templates": snapshot.resource_templates,
        }[kind]
        return offerings_to_wire(items, wire_type)

    async def on_list_prompts(
        self,
        context: MiddlewareContext[ListPromptsRequest],
        call_next: CallNext[ListPromptsRequest, Sequence[Any]],
    ) -> Sequence[Any]:
        _ = call_next
        return await self._cached_offerings(context, kind="prompts", wire_type=Prompt)

    async def on_list_resources(
        self,
        context: MiddlewareContext[ListResourcesRequest],
        call_next: CallNext[ListResourcesRequest, Sequence[Any]],
    ) -> Sequence[Any]:
        _ = call_next
        return await self._cached_offerings(context, kind="resources", wire_type=Resource)

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[ListResourceTemplatesRequest],
        call_next: CallNext[ListResourceTemplatesRequest, Sequence[Any]],
    ) -> Sequence[Any]:
        _ = call_next
        return await self._cached_offerings(
            context,
            kind="resource_templates",
            wire_type=ResourceTemplate,
        )

    async def _proxy_offering_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> object:
        runtime = await self._resolve_runtime(context)
        self._coordinator.ensure_backends_mounted(runtime.config.mcp_servers)
        return await call_next(context)

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

        enter_tools_list()
        try:
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
                        max_wait_seconds=3.0,
                    )
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
                return []
            finally:
                reset_session_scoping_active(scoping_token)
                reset_current_session_runtime(token)
        finally:
            exit_tools_list()

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

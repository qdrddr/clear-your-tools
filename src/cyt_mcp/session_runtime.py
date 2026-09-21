"""Per-session / per-workspace cyt-mcp runtime state for multi-window Cursor."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastmcp import FastMCP
from mcp.server.session import ServerSession

from cyt_mcp.backends import ensure_backend_servers_mounted
from cyt_mcp.catalog_build import hydrate_runtime_cache, refresh_catalog_cache
from cyt_mcp.config import AggregatorConfig, load_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.offerings_cache import OfferingsCache
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.tool_identity import tool_name_allowed_for_servers

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkspaceSessionRuntime:
    workspace_root: Path
    config: AggregatorConfig
    cache: RuntimeToolCache
    config_holder: ConfigHolder


def root_uri_to_path(uri: str) -> Path | None:
    text = str(uri or "").strip()
    if not text:
        return None
    if len(text) >= 2 and text[1] == ":":
        try:
            return Path(unquote(text))
        except OSError:
            return None
    parsed = urlparse(text)
    if parsed.scheme in ("", "file"):
        raw_path = unquote(parsed.path if parsed.scheme == "file" else text)
        if not raw_path:
            return None
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        try:
            return Path(raw_path)
        except OSError:
            return None
    return None


def _canonical_workspace(path: Path) -> Path | None:
    from cyt.tiers.config import resolve_git_toplevel

    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    git_root = resolve_git_toplevel(resolved)
    return git_root or resolved


async def resolve_session_workspace_root(
    session: ServerSession,
    *,
    fallback: Path | None,
) -> Path | None:
    # Do NOT call session.list_roots() here. Cursor's shared MCP host returns roots
    # asynchronously and may include multiple workspaces in one response; late or
    # orphaned JSON-RPC replies corrupt the stdio stream (unknown request ID /
    # Connection closed). Per-window workspace comes from CYT_WORKSPACE / bootstrap.
    _ = session
    env_ws = str(os.environ.get("CYT_WORKSPACE") or "").strip()
    if env_ws:
        canonical = _canonical_workspace(Path(env_ws))
        if canonical is not None:
            return canonical
    if fallback is not None:
        try:
            resolved = fallback.expanduser().resolve()
            if resolved.is_dir():
                return resolved
        except OSError:
            pass
    return None


def tool_belongs_to_runtime(tool_name: str, runtime: WorkspaceSessionRuntime) -> bool:
    return tool_name_allowed_for_servers(tool_name, runtime.config.mcp_servers.keys())


class MultiWorkspaceCoordinator:
    """Bind MCP sessions to workspace-specific catalogs within one cyt-mcp process."""

    def __init__(
        self,
        server: FastMCP,
        bootstrap: WorkspaceSessionRuntime,
        *,
        agent: str,
        aggregator_path: Path | None,
    ) -> None:
        self._server = server
        self._bootstrap = bootstrap
        self._agent = agent
        self._aggregator_path = aggregator_path
        self._lock = threading.Lock()
        self._async_lock = asyncio.Lock()
        self._runtimes: dict[str, WorkspaceSessionRuntime] = {}
        self._session_bindings: dict[str, str] = {}
        self._mounted_servers: set[str] = set()
        self._offerings_cache = OfferingsCache()
        bootstrap_key = self._runtime_key(bootstrap.workspace_root)
        if bootstrap_key is not None:
            self._runtimes[bootstrap_key] = bootstrap

    @property
    def bootstrap(self) -> WorkspaceSessionRuntime:
        return self._bootstrap

    @property
    def server(self) -> FastMCP:
        return self._server

    @property
    def offerings_cache(self) -> OfferingsCache:
        return self._offerings_cache

    @staticmethod
    def _runtime_key(workspace_root: Path | None) -> str | None:
        if workspace_root is None:
            return None
        canonical = _canonical_workspace(workspace_root) or workspace_root
        try:
            return str(canonical.expanduser().resolve())
        except OSError:
            return str(workspace_root)

    def ensure_backends_mounted(self, mcp_servers: dict[str, Any]) -> list[str]:
        """Mount backend MCP proxies on first tools/call or catalog refresh."""
        degraded = ensure_backend_servers_mounted(self._server, mcp_servers)
        self._mounted_servers = getattr(self._server, "_cyt_mcp_mounted_servers", set())
        return degraded

    def _bootstrap_covers(self, workspace_root: Path) -> bool:
        bootstrap_key = self._runtime_key(self._bootstrap.workspace_root)
        session_key = self._runtime_key(workspace_root)
        return (
            bootstrap_key is not None
            and session_key is not None
            and bootstrap_key == session_key
        )

    def runtime_for_session_key(self, session_key: str) -> WorkspaceSessionRuntime:
        ws_key = self._session_bindings.get(session_key)
        if ws_key is not None:
            runtime = self._runtimes.get(ws_key)
            if runtime is not None:
                return runtime
        return self._bootstrap

    async def bind_session(
        self,
        session: ServerSession,
        *,
        session_key: str,
    ) -> WorkspaceSessionRuntime:
        if self._bootstrap.config.catalog_scope == "user":
            self._session_bindings[session_key] = "user"
            return self._bootstrap
        fallback = self._bootstrap.config.workspace_root
        workspace_root = await resolve_session_workspace_root(session, fallback=fallback)
        if workspace_root is None or self._bootstrap_covers(workspace_root):
            runtime = self._bootstrap
            binding_key = self._runtime_key(runtime.workspace_root) or "bootstrap"
            self._session_bindings[session_key] = binding_key
            return runtime
        runtime = await self.ensure_runtime(workspace_root)
        binding_key = self._runtime_key(runtime.workspace_root) or str(runtime.workspace_root)
        self._session_bindings[session_key] = binding_key
        return runtime

    async def ensure_runtime(self, workspace_root: Path) -> WorkspaceSessionRuntime:
        if self._bootstrap_covers(workspace_root):
            return self._bootstrap
        key = self._runtime_key(workspace_root)
        if key is None:
            return self._bootstrap
        existing = self._runtimes.get(key)
        if existing is not None:
            return existing
        async with self._async_lock:
            existing = self._runtimes.get(key)
            if existing is not None:
                return existing
            config = load_aggregator_config(
                agent=self._agent,
                aggregator_path=self._aggregator_path,
                workspace_folder=workspace_root,
            )
            self.ensure_backends_mounted(config.mcp_servers)
            cache = RuntimeToolCache()
            config_holder = ConfigHolder(config)
            hydrate_runtime_cache(cache, config)
            runtime = WorkspaceSessionRuntime(
                workspace_root=workspace_root,
                config=config,
                cache=cache,
                config_holder=config_holder,
            )
            self._register_push_context(runtime)
            with self._lock:
                self._runtimes[key] = runtime
            if not cache.snapshot():

                async def _background_runtime_refresh() -> None:
                    try:
                        await refresh_catalog_cache(self._server, cache, config)
                        runtime_key = self._runtime_key(workspace_root)
                        if runtime_key is not None:

                            async def _refresh_offerings() -> None:
                                from cyt_mcp.catalog_build import disk_catalog_slug_for_config

                                await self._offerings_cache.refresh_from_server(
                                    self._server,
                                    runtime_key=runtime_key,
                                    mcp_servers=config.mcp_servers,
                                    ensure_mounted=self.ensure_backends_mounted,
                                    disk_slug=disk_catalog_slug_for_config(config),
                                )

                            self._offerings_cache.schedule_refresh_once(
                                runtime_key=runtime_key,
                                delay_s=0.0,
                                coro_factory=_refresh_offerings,
                            )
                        from cyt_mcp.tool_list_notify import notify_all_sessions_list_changed
                        from cyt_mcp.hook_daemon_push import _push_contexts, _instance_key

                        ctx_key = _instance_key(config)
                        ctx = _push_contexts.get(ctx_key)
                        if ctx is not None and ctx.list_changed_middleware is not None:
                            await notify_all_sessions_list_changed(ctx.list_changed_middleware)
                    except Exception as exc:
                        logger.warning("cyt-mcp workspace runtime refresh failed: %s", exc)

                asyncio.create_task(
                    _background_runtime_refresh(),
                    name=f"cyt-mcp-runtime-refresh-{key}",
                )
            return runtime

    def _register_push_context(self, runtime: WorkspaceSessionRuntime) -> None:
        from cyt_mcp.hook_daemon_push import PushContext, _instance_key, _push_contexts, register_push_context

        key = _instance_key(runtime.config_holder.config)
        existing = _push_contexts.get(key)
        register_push_context(
            PushContext(
                config_holder=runtime.config_holder,
                cache=runtime.cache,
                server=self._server,
                list_changed_middleware=(
                    existing.list_changed_middleware if existing is not None else None
                ),
            ),
        )

    async def refresh_runtime_catalog(self, runtime: WorkspaceSessionRuntime) -> None:
        await refresh_catalog_cache(self._server, runtime.cache, runtime.config)

    def session_keys_for_workspace(self, workspace_root: Path) -> list[str]:
        target = str(workspace_root.expanduser().resolve())
        return [key for key, ws in self._session_bindings.items() if ws == target]

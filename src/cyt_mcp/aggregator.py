"""Assemble cyt-mcp FastMCP server."""

from __future__ import annotations

import logging
from pathlib import Path

from fastmcp import FastMCP

from cyt_mcp.config import frontend_server_name_for_scope
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import register_search_tool
from cyt_mcp.session_runtime import MultiWorkspaceCoordinator, WorkspaceSessionRuntime
from cyt_mcp.session_workspace_middleware import register_session_workspace_middleware
from cyt_mcp.stubs import StubListTransform
from cyt_mcp.tool_list_notify import (
    ToolListChangedMiddleware,
    register_tool_list_changed_middleware,
)
from cyt_mcp.tool_use_feedback_middleware import register_tool_use_feedback_middleware

logger = logging.getLogger(__name__)


def build_aggregator(
    config_holder: ConfigHolder,
    cache: RuntimeToolCache,
    *,
    aggregator_path: Path | None = None,
) -> tuple[FastMCP, ToolListChangedMiddleware | None, MultiWorkspaceCoordinator]:
    config = config_holder.config
    server = FastMCP(frontend_server_name_for_scope(config.catalog_scope))
    bootstrap_root = config.workspace_root or Path.cwd()
    coordinator = MultiWorkspaceCoordinator(
        server,
        WorkspaceSessionRuntime(
            workspace_root=bootstrap_root,
            config=config,
            cache=cache,
            config_holder=config_holder,
        ),
        agent=config.agent,
        aggregator_path=aggregator_path,
    )
    list_changed_middleware = None
    if not config.verify_only:
        register_session_workspace_middleware(server, coordinator)
        register_search_tool(server, cache, agent=config.agent)
        server.add_transform(
            StubListTransform(
                cache,
                retain=config.stub_retain,
                config_holder=config_holder,
            ),
        )
        list_changed_middleware = register_tool_list_changed_middleware(
            server,
            cache,
            config_holder,
            coordinator=coordinator,
        )
        from cyt.config import load_config
        from cyt.tiers.config import tier_tool_capture_source

        cyt_config = load_config()
        if tier_tool_capture_source(cyt_config) == "cyt_mcp":
            register_tool_use_feedback_middleware(
                server,
                cache,
                config_holder,
                config=config,
            )
    return server, list_changed_middleware, coordinator

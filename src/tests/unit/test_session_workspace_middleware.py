"""Tests for session workspace middleware tools/list behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastmcp.tools.base import Tool
from mcp.types import ListToolsRequest

from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME, register_search_tool
from cyt_mcp.session_runtime import MultiWorkspaceCoordinator, WorkspaceSessionRuntime
from cyt_mcp.session_workspace_middleware import SessionWorkspaceMiddleware


def test_on_list_tools_falls_back_to_call_next_when_cache_empty(tmp_path) -> None:
    """Reload must not return [] when only get-tool-definitions is registered."""
    from fastmcp import FastMCP

    config = sample_aggregator_config(
        catalog_scope="user",
        workspace_root=tmp_path,
    )
    cache = RuntimeToolCache()
    server = FastMCP("cyt-mcp-usr")
    register_search_tool(server, cache, agent=config.agent)
    bootstrap = WorkspaceSessionRuntime(
        workspace_root=tmp_path,
        config=config,
        cache=cache,
        config_holder=ConfigHolder(config),
    )
    coordinator = MultiWorkspaceCoordinator(
        server,
        bootstrap,
        agent=config.agent,
        aggregator_path=None,
    )
    middleware = SessionWorkspaceMiddleware(coordinator)

    fallback_tool = Tool(
        name=MCP_WIRE_SEARCH_TOOL_NAME,
        description="search",
        parameters={"type": "object", "properties": {}},
    )
    call_next = AsyncMock(return_value=[fallback_tool])
    context = MagicMock()
    context.method = "tools/list"
    context.message = ListToolsRequest(method="tools/list")
    context.fastmcp_context = None

    with patch.object(coordinator, "ensure_backends_mounted"):
        tools = asyncio.run(middleware.on_list_tools(context, call_next))

    assert tools == [fallback_tool]
    call_next.assert_awaited_once_with(context)


def test_on_list_tools_returns_stubs_when_cache_populated(tmp_path) -> None:
    """Workspace reload should serve stub projections from a populated runtime cache."""
    from fastmcp import FastMCP

    config = sample_aggregator_config(
        catalog_scope="workspace",
        workspace_root=tmp_path,
    )
    cache = RuntimeToolCache()
    cache.replace(
        [
            {
                "name": "gitnexus_cypher",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "description": "Run Cypher against GitNexus",
            },
        ],
    )
    server = FastMCP("cyt-mcp-ws")
    register_search_tool(server, cache, agent=config.agent)
    bootstrap = WorkspaceSessionRuntime(
        workspace_root=tmp_path,
        config=config,
        cache=cache,
        config_holder=ConfigHolder(config),
    )
    coordinator = MultiWorkspaceCoordinator(
        server,
        bootstrap,
        agent=config.agent,
        aggregator_path=None,
    )
    middleware = SessionWorkspaceMiddleware(coordinator)

    call_next = AsyncMock(return_value=[])
    context = MagicMock()
    context.method = "tools/list"
    context.message = ListToolsRequest(method="tools/list")
    context.fastmcp_context = None

    with patch.object(coordinator, "ensure_backends_mounted"):
        tools = asyncio.run(middleware.on_list_tools(context, call_next))

    names = {tool.to_mcp_tool().name for tool in tools}
    assert "gitnexus_cypher" in names
    assert MCP_WIRE_SEARCH_TOOL_NAME in names
    call_next.assert_not_awaited()


def test_on_list_tools_user_scope_never_returns_empty_without_call_next(tmp_path) -> None:
    """Regression: cold user cache must not short-circuit to []."""
    from fastmcp import FastMCP

    config = sample_aggregator_config(
        catalog_scope="user",
        workspace_root=tmp_path,
    )
    cache = RuntimeToolCache()
    server = FastMCP("cyt-mcp-usr")
    register_search_tool(server, cache, agent=config.agent)
    bootstrap = WorkspaceSessionRuntime(
        workspace_root=tmp_path,
        config=config,
        cache=cache,
        config_holder=ConfigHolder(config),
    )
    coordinator = MultiWorkspaceCoordinator(
        server,
        bootstrap,
        agent=config.agent,
        aggregator_path=None,
    )
    middleware = SessionWorkspaceMiddleware(coordinator)

    call_next = AsyncMock(return_value=[])
    context = MagicMock()
    context.method = "tools/list"
    context.message = ListToolsRequest(method="tools/list")
    context.fastmcp_context = None

    with patch.object(coordinator, "ensure_backends_mounted"):
        tools = asyncio.run(middleware.on_list_tools(context, call_next))

    assert tools == []
    call_next.assert_awaited_once_with(context)

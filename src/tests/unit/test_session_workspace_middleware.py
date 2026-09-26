"""Tests for session workspace middleware tools/list behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastmcp.tools.base import Tool
from mcp.types import (
    ListPromptsRequest,
    ListResourcesRequest,
    ListResourceTemplatesRequest,
    ListToolsRequest,
    Prompt,
    Resource,
    ResourceTemplate,
)
from pydantic.networks import AnyUrl

from cyt_mcp.config import CatalogScope, sample_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME, register_search_tool
from cyt_mcp.session_runtime import MultiWorkspaceCoordinator, WorkspaceSessionRuntime
from cyt_mcp.session_workspace_middleware import SessionWorkspaceMiddleware


def _list_offering_cases() -> tuple[
    tuple[str, str, ListPromptsRequest | ListResourcesRequest | ListResourceTemplatesRequest],
    ...,
]:
    return (
        ("prompts/list", "on_list_prompts", ListPromptsRequest(method="prompts/list")),
        ("resources/list", "on_list_resources", ListResourcesRequest(method="resources/list")),
        (
            "resources/templates/list",
            "on_list_resource_templates",
            ListResourceTemplatesRequest(method="resources/templates/list"),
        ),
    )


def test_on_list_tools_falls_back_to_call_next_when_cache_empty(tmp_path: Path) -> None:
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


def test_on_list_tools_returns_stubs_when_cache_populated(tmp_path: Path) -> None:
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


def test_on_list_tools_user_scope_never_returns_empty_without_call_next(tmp_path: Path) -> None:
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


def _middleware_for_scope(
    tmp_path: Path,
    *,
    catalog_scope: CatalogScope,
) -> tuple[SessionWorkspaceMiddleware, MultiWorkspaceCoordinator]:
    from fastmcp import FastMCP

    config = sample_aggregator_config(
        catalog_scope=catalog_scope,
        workspace_root=tmp_path,
    )
    cache = RuntimeToolCache()
    server = FastMCP(f"cyt-mcp-{catalog_scope[:3]}")
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
    return SessionWorkspaceMiddleware(coordinator), coordinator


def test_session_workspace_middleware_has_no_cached_offerings_helper() -> None:
    assert not hasattr(SessionWorkspaceMiddleware, "_cached_offerings")


def test_offerings_cache_module_removed() -> None:
    import importlib.util

    assert importlib.util.find_spec("cyt_mcp.offerings_cache") is None, (
        "offerings cache must stay removed for live passthrough"
    )


def test_on_list_prompts_proxies_to_backends(tmp_path: Path) -> None:
    middleware, coordinator = _middleware_for_scope(tmp_path, catalog_scope="user")
    backend_prompt = Prompt(name="review", description="Review code")
    call_next = AsyncMock(return_value=[backend_prompt])
    context = MagicMock()
    context.method = "prompts/list"
    context.message = ListPromptsRequest(method="prompts/list")
    context.fastmcp_context = None

    with patch.object(coordinator, "ensure_backends_mounted"):
        prompts = asyncio.run(middleware.on_list_prompts(context, call_next))

    assert prompts == [backend_prompt]
    call_next.assert_awaited_once_with(context)


def test_on_list_resources_proxies_to_backends(tmp_path: Path) -> None:
    middleware, coordinator = _middleware_for_scope(tmp_path, catalog_scope="user")
    backend_resource = Resource(uri=AnyUrl("gitnexus://repo/demo"), name="demo")
    call_next = AsyncMock(return_value=[backend_resource])
    context = MagicMock()
    context.method = "resources/list"
    context.message = ListResourcesRequest(method="resources/list")
    context.fastmcp_context = None

    with patch.object(coordinator, "ensure_backends_mounted"):
        resources = asyncio.run(middleware.on_list_resources(context, call_next))

    assert resources == [backend_resource]
    call_next.assert_awaited_once_with(context)


def test_on_list_resource_templates_proxies_to_backends(tmp_path: Path) -> None:
    middleware, coordinator = _middleware_for_scope(tmp_path, catalog_scope="user")
    backend_template = ResourceTemplate(
        uriTemplate="gitnexus://repo/{name}",
        name="repo-template",
    )
    call_next = AsyncMock(return_value=[backend_template])
    context = MagicMock()
    context.method = "resources/templates/list"
    context.message = ListResourceTemplatesRequest(method="resources/templates/list")
    context.fastmcp_context = None

    with patch.object(coordinator, "ensure_backends_mounted"):
        templates = asyncio.run(middleware.on_list_resource_templates(context, call_next))

    assert templates == [backend_template]
    call_next.assert_awaited_once_with(context)


def test_list_offering_handlers_never_ignore_call_next(tmp_path: Path) -> None:
    middleware, coordinator = _middleware_for_scope(tmp_path, catalog_scope="user")

    for method, handler_name, message in _list_offering_cases():
        handler = getattr(middleware, handler_name)
        sentinel = object()
        call_next = AsyncMock(return_value=[sentinel])
        context = MagicMock()
        context.method = method
        context.message = message
        context.fastmcp_context = None

        with patch.object(coordinator, "ensure_backends_mounted"):
            result = asyncio.run(handler(context, call_next))

        assert result == [sentinel], f"{method} must proxy via call_next"
        call_next.assert_awaited_once_with(context)
        call_next.reset_mock()

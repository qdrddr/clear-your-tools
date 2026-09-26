"""Integration tests: MCP prompts/resources/templates live-proxy through cyt-mcp."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME
from tests.support.cyt_mcp_offerings_passthrough_fixtures import (
    build_aggregator_with_offerings_backend,
    list_offerings_via_middleware,
    load_offerings_passthrough_scenarios,
    make_offerings_list_context,
)


def _wire_prompt_names(items: Sequence[Any]) -> list[str]:
    return [str(getattr(item, "name", item)) for item in items]


def _wire_resource_uris(items: Sequence[Any]) -> list[str]:
    return [str(getattr(item, "uri", item)) for item in items]


def _wire_template_names(items: Sequence[Any]) -> list[str]:
    return [str(getattr(item, "name", item)) for item in items]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_middleware_list_prompts_proxies_mounted_backend_on_cold_start() -> None:
    server, middleware, _coordinator, _spec = build_aggregator_with_offerings_backend()
    scenario = next(
        item
        for item in load_offerings_passthrough_scenarios()
        if item.id == "cold_start_list_prompts"
    )

    prompts = await list_offerings_via_middleware(middleware, server, method=scenario.method)

    assert _wire_prompt_names(prompts) == list(scenario.expected_prompt_names)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_middleware_list_resources_proxies_mounted_backend_on_cold_start() -> None:
    server, middleware, _coordinator, _spec = build_aggregator_with_offerings_backend()
    scenario = next(
        item
        for item in load_offerings_passthrough_scenarios()
        if item.id == "cold_start_list_resources"
    )

    resources = await list_offerings_via_middleware(middleware, server, method=scenario.method)

    assert _wire_resource_uris(resources) == list(scenario.expected_resource_uris)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_middleware_list_resource_templates_proxies_mounted_backend_on_cold_start() -> None:
    server, middleware, _coordinator, _spec = build_aggregator_with_offerings_backend()
    scenario = next(
        item
        for item in load_offerings_passthrough_scenarios()
        if item.id == "cold_start_list_resource_templates"
    )

    templates = await list_offerings_via_middleware(middleware, server, method=scenario.method)

    assert _wire_template_names(templates) == list(scenario.expected_template_names)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_list_still_serves_stubs_while_offerings_passthrough() -> None:
    scenario = next(
        item
        for item in load_offerings_passthrough_scenarios()
        if item.id == "tools_list_still_stubbed"
    )
    assert scenario.cached_tool_name is not None
    _server, middleware, coordinator, _spec = build_aggregator_with_offerings_backend(
        cache_tools=[
            {
                "name": scenario.cached_tool_name,
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "description": "Run Cypher against GitNexus",
            },
        ],
    )

    call_next = AsyncMock(return_value=[])
    context = make_offerings_list_context(scenario.method)
    with patch.object(coordinator, "ensure_backends_mounted"):
        tools = await middleware.on_list_tools(context, call_next)

    names = {tool.to_mcp_tool().name for tool in tools}
    assert scenario.cached_tool_name in names
    assert MCP_WIRE_SEARCH_TOOL_NAME in names
    call_next.assert_not_awaited()

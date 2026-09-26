"""Gherkin steps for cyt-mcp MCP offerings passthrough regression."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME
from tests.support.cyt_mcp_offerings_passthrough_fixtures import (
    build_aggregator_with_offerings_backend,
    list_offerings_via_middleware,
    make_offerings_list_context,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "cyt_mcp_offerings_passthrough.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given("a cyt-mcp aggregator with mounted backend offerings")
def given_aggregator_with_offerings(gherkin_context: GherkinContext) -> None:
    server, middleware, coordinator, _spec = build_aggregator_with_offerings_backend()
    gherkin_context.payload = {
        "server": server,
        "middleware": middleware,
        "coordinator": coordinator,
    }


@given("a cyt-mcp aggregator with mounted backend offerings and populated tool cache")
def given_aggregator_with_offerings_and_tool_cache(gherkin_context: GherkinContext) -> None:
    server, middleware, coordinator, _spec = build_aggregator_with_offerings_backend(
        cache_tools=[
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
    gherkin_context.payload = {
        "server": server,
        "middleware": middleware,
        "coordinator": coordinator,
    }


@when("middleware handles prompts/list on a cold start")
def when_list_prompts_cold(gherkin_context: GherkinContext) -> None:
    payload = gherkin_context.payload
    gherkin_context.payload["result"] = asyncio.run(
        list_offerings_via_middleware(
            payload["middleware"],
            payload["server"],
            method="prompts/list",
        ),
    )


@when("middleware handles resources/list on a cold start")
def when_list_resources_cold(gherkin_context: GherkinContext) -> None:
    payload = gherkin_context.payload
    gherkin_context.payload["result"] = asyncio.run(
        list_offerings_via_middleware(
            payload["middleware"],
            payload["server"],
            method="resources/list",
        ),
    )


@when("middleware handles resources/templates/list on a cold start")
def when_list_templates_cold(gherkin_context: GherkinContext) -> None:
    payload = gherkin_context.payload
    gherkin_context.payload["result"] = asyncio.run(
        list_offerings_via_middleware(
            payload["middleware"],
            payload["server"],
            method="resources/templates/list",
        ),
    )


@when("middleware handles tools/list")
def when_list_tools(gherkin_context: GherkinContext) -> None:
    payload = gherkin_context.payload
    middleware = payload["middleware"]
    call_next = AsyncMock(return_value=[])
    context = make_offerings_list_context("tools/list")
    payload["call_next"] = call_next
    payload["result"] = asyncio.run(middleware.on_list_tools(context, call_next))


@then(parsers.parse("prompts/list should return backend prompt names {name}"))
def then_prompt_names(name: str, gherkin_context: GherkinContext) -> None:
    prompts = gherkin_context.payload["result"]
    names = [str(getattr(item, "name", item)) for item in prompts]
    assert names == [name]


@then(parsers.parse("resources/list should return backend resource uri {uri}"))
def then_resource_uri(uri: str, gherkin_context: GherkinContext) -> None:
    resources = gherkin_context.payload["result"]
    uris = [str(getattr(item, "uri", item)) for item in resources]
    assert uris == [uri]


@then(
    parsers.parse(
        "resource templates/list should return backend template name {name}",
    ),
)
def then_template_name(name: str, gherkin_context: GherkinContext) -> None:
    templates = gherkin_context.payload["result"]
    names = [str(getattr(item, "name", item)) for item in templates]
    assert names == [name]


@then(
    parsers.parse("tools/list should serve stub projections including {tool_name}"),
)
def then_tools_include_stub(tool_name: str, gherkin_context: GherkinContext) -> None:
    tools = gherkin_context.payload["result"]
    names = {tool.to_mcp_tool().name for tool in tools}
    assert tool_name in names
    assert MCP_WIRE_SEARCH_TOOL_NAME in names


@then("tools/list should not proxy to backend tools/list")
def then_tools_do_not_proxy(gherkin_context: GherkinContext) -> None:
    call_next = gherkin_context.payload["call_next"]
    call_next.assert_not_awaited()

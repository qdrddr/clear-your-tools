"""Gherkin steps for cyt-mcp_search tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP
from fastmcp.tools.tool import Tool
from pytest_bdd import given, parsers, scenarios, then, when

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import SEARCH_TOOL_NAME, lookup_tool_definition, register_search_tool
from cyt_mcp.stubs import StubListTransform
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "cyt_mcp_search.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin

_GRAPH_TOOL = "codebase-memory-mcp_search_graph"
_QUERY_TOOL = "codebase-memory-mcp_query_graph"


def _full_graph_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"project": {"type": "string"}},
        "required": ["project"],
    }


@given(parsers.parse("a runtime cache with tool {name} and full inputSchema"))
def given_cache_with_tool(name: str, gherkin_context: GherkinContext) -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [
            {
                "name": name,
                "inputSchema": _full_graph_schema(),
                "description": "search graph",
            },
        ],
    )
    gherkin_context.payload = {"cache": cache, "tool_name": name}


@given(parsers.parse("a search index entry for {name} with outputSchema"))
def given_search_index(name: str, gherkin_context: GherkinContext) -> None:
    cache: RuntimeToolCache = gherkin_context.payload["cache"]
    cache.replace(
        cache.snapshot(),
        search_index={
            name: {
                "name": name,
                "inputSchema": _full_graph_schema(),
                "outputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
                "meta": {"source": "test"},
            },
        },
    )


@given(parsers.parse("a runtime cache with tool {name}"))
def given_cache_tool_only(name: str, gherkin_context: GherkinContext) -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [
            {
                "name": name,
                "inputSchema": _full_graph_schema(),
            },
        ],
        search_index={
            name: {
                "name": name,
                "inputSchema": _full_graph_schema(),
            },
        },
    )
    gherkin_context.payload = {"cache": cache, "tool_name": name}


@given(parsers.parse("a runtime cache with only backend tool {name}"))
def given_cache_single_backend(name: str, gherkin_context: GherkinContext) -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [
            {
                "name": name,
                "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        ],
        search_index={
            name: {
                "name": name,
                "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        },
    )
    gherkin_context.payload = {"cache": cache}


@given("cyt-mcp_search tool and backend tool codebase-memory-mcp_search_graph")
def given_search_and_backend(gherkin_context: GherkinContext) -> None:
    from cyt_mcp.search import refresh_search_tool_schema

    cache = RuntimeToolCache()
    server = FastMCP("cyt-mcp-test")
    register_search_tool(server, cache, agent="cursor")
    backend = Tool.from_function(
        lambda project: project,
        name=_GRAPH_TOOL,
    )
    backend = backend.model_copy(update={"description": "graph search"})
    cache.replace(
        [
            {
                "name": _GRAPH_TOOL,
                "inputSchema": _full_graph_schema(),
                "description": "graph search",
            },
        ],
        search_index={
            _GRAPH_TOOL: {
                "name": _GRAPH_TOOL,
                "inputSchema": _full_graph_schema(),
                "description": "graph search",
            },
        },
    )
    refresh_search_tool_schema(cache)
    search_tool = cache.search_tool()
    assert search_tool is not None
    gherkin_context.payload = {
        "cache": cache,
        "server": server,
        "backend_tool": backend,
        "search_tool": search_tool,
        "transform": StubListTransform(cache, include_description=False),
    }


@when(parsers.parse("cyt-mcp_search is called with tool_name {tool_name}"))
def when_search_called(tool_name: str, gherkin_context: GherkinContext) -> None:
    cache: RuntimeToolCache = gherkin_context.payload["cache"]
    try:
        gherkin_context.payload["search_result"] = lookup_tool_definition(cache, tool_name)
        gherkin_context.payload["search_error"] = None
    except ValueError as exc:
        gherkin_context.payload["search_result"] = None
        gherkin_context.payload["search_error"] = str(exc)


@when("catalog payload is exported for agent cursor")
def when_catalog_export(gherkin_context: GherkinContext) -> None:
    from cyt_mcp.catalog import catalog_payload

    gherkin_context.payload["catalog"] = catalog_payload(
        gherkin_context.payload["cache"],
        agent="cursor",
    )


@when("stub list transform runs for agent cursor without descriptions")
async def _run_stub_transform(gherkin_context: GherkinContext) -> None:
    payload = gherkin_context.payload
    stubs = await payload["transform"].list_tools(
        [payload["search_tool"], payload["backend_tool"]],
    )
    payload["stubs"] = stubs


@when("stub list transform runs for agent cursor without descriptions")
def when_stub_transform(gherkin_context: GherkinContext) -> None:
    import asyncio

    asyncio.run(_run_stub_transform(gherkin_context))


@when(parsers.parse("cyt-mcp search CLI is run with --json for {tool_name}"))
def when_search_cli(
    tool_name: str,
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cyt_mcp.cli import main

    async def _fake_refresh(_server: object, cache: object, _config: object) -> None:
        from cyt_mcp.runtime_cache import RuntimeToolCache

        assert isinstance(cache, RuntimeToolCache)
        cache.replace(
            [
                {
                    "name": tool_name,
                    "inputSchema": _full_graph_schema(),
                },
            ],
            search_index={
                tool_name: {
                    "name": tool_name,
                    "inputSchema": _full_graph_schema(),
                },
            },
        )

    monkeypatch.setattr("cyt_mcp.cli.refresh_runtime_cache", _fake_refresh)
    exit_code = main(["search", "--json", tool_name])
    captured = capsys.readouterr()
    gherkin_context.payload["cli_exit_code"] = exit_code
    gherkin_context.payload["cli_stdout"] = captured.out


@then(parsers.parse("search result should include full inputSchema for {name}"))
def then_search_has_schema(name: str, gherkin_context: GherkinContext) -> None:
    result = gherkin_context.payload["search_result"]
    assert result is not None
    assert result["name"] == name
    assert result["inputSchema"] == _full_graph_schema()


@then("search result should include outputSchema annotations and meta when indexed")
def then_search_has_metadata(gherkin_context: GherkinContext) -> None:
    result = gherkin_context.payload["search_result"]
    assert result is not None
    assert "outputSchema" in result
    assert "annotations" in result
    assert "meta" in result


@then("search should fail with self-lookup error")
def then_self_lookup_error(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["search_error"]
    assert "itself" in gherkin_context.payload["search_error"].lower()


@then("search should fail with unknown tool error")
def then_unknown_tool_error(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["search_error"]
    assert "unknown tool" in gherkin_context.payload["search_error"]


@then("catalog tool names should not include cyt-mcp_search")
def then_catalog_excludes_search(gherkin_context: GherkinContext) -> None:
    names = [tool["name"] for tool in gherkin_context.payload["catalog"]["tools"]]
    assert SEARCH_TOOL_NAME not in names


@then(parsers.parse("catalog tool names should include {name}"))
def then_catalog_includes(name: str, gherkin_context: GherkinContext) -> None:
    names = [tool["name"] for tool in gherkin_context.payload["catalog"]["tools"]]
    assert name in names


@then("cyt-mcp_search stub should retain tool_name enum schema and description")
def then_search_stub_full(gherkin_context: GherkinContext) -> None:
    stubs = gherkin_context.payload["stubs"]
    search_tools = [stub for stub in stubs if stub.to_mcp_tool().name == SEARCH_TOOL_NAME]
    assert search_tools
    mcp_tool = search_tools[0].to_mcp_tool()
    assert mcp_tool.description
    enum_values = mcp_tool.inputSchema["properties"]["tool_name"]["enum"]
    assert _GRAPH_TOOL in enum_values


@then("backend stub should have minimal empty inputSchema")
def then_backend_stub_minimal(gherkin_context: GherkinContext) -> None:
    stubs = gherkin_context.payload["stubs"]
    backend = next(stub for stub in stubs if stub.to_mcp_tool().name == _GRAPH_TOOL)
    assert backend.to_mcp_tool().inputSchema == {"type": "object", "properties": {}}


@then("cyt-mcp_search should not be stored in runtime catalog cache")
def then_search_not_in_catalog_cache(gherkin_context: GherkinContext) -> None:
    cache: RuntimeToolCache = gherkin_context.payload["cache"]
    names = [tool["name"] for tool in cache.snapshot()]
    assert SEARCH_TOOL_NAME not in names


@then("CLI stdout should be valid JSON with full inputSchema")
def then_cli_json(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["cli_exit_code"] == 0
    data = json.loads(gherkin_context.payload["cli_stdout"])
    assert data["inputSchema"] == _full_graph_schema()

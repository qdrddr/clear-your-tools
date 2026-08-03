"""Gherkin steps for cyt-mcp aggregator (stubs, catalog, fault isolation)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from fastmcp.tools.tool import Tool
from pytest_bdd import given, parsers, scenarios, then, when

from cyt_mcp.aggregator import build_aggregator
from cyt_mcp.catalog import catalog_payload
from cyt_mcp.config import AggregatorConfig, HttpSettings
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.stubs import StubListTransform
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "cyt_mcp_aggregator.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


def _sample_config(*, mcp_servers: dict[str, Any] | None = None) -> AggregatorConfig:
    return AggregatorConfig(
        agent="cursor",
        mcp_servers=mcp_servers or {},
        transport="stdio",
        http=HttpSettings(
            host="127.0.0.1",
            port=8765,
            mcp_path="/mcp",
            catalog_path="/catalog",
        ),
        codex_stubs_include_description=False,
        aggregator_path=Path("~/.config/cyt/mcp-aggregator.yaml"),
        agent_mcp_path=Path("~/.config/cyt/mcp/cursor.json"),
    )


@given(parsers.parse("a cached FastMCP tool named {name} with a full schema"))
def given_cached_tool(name: str, gherkin_context: GherkinContext) -> None:
    tool = Tool.from_function(lambda path: path, name=name)
    cache = RuntimeToolCache()
    transform = StubListTransform(cache, include_description=False)
    gherkin_context.payload = {
        "cache": cache,
        "transform": transform,
        "tool": tool,
        "name": name,
    }


@when("stub list transform runs without descriptions")
def when_stub_transform(gherkin_context: GherkinContext) -> None:
    payload = gherkin_context.payload
    stubs = asyncio.run(payload["transform"].list_tools([payload["tool"]]))
    gherkin_context.payload["stubs"] = stubs
    gherkin_context.payload["catalog"] = catalog_payload(
        payload["cache"],
        agent="cursor",
    )


@then("stub inputSchema should be a minimal empty object schema")
def then_minimal_stub_schema(gherkin_context: GherkinContext) -> None:
    stub = gherkin_context.payload["stubs"][0]
    mcp_tool = stub.to_mcp_tool()
    assert mcp_tool.inputSchema == {"type": "object", "properties": {}}


@then(parsers.parse("catalog export should preserve the full tool name {name}"))
def then_catalog_name(name: str, gherkin_context: GherkinContext) -> None:
    tools = gherkin_context.payload["catalog"]["tools"]
    assert tools[0]["name"] == name


@given("a runtime cache with tools filesystem_read_file and context7_query")
def given_runtime_cache(gherkin_context: GherkinContext) -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [
            {
                "name": "filesystem_read_file",
                "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
            {
                "name": "context7_query",
                "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        ],
    )
    gherkin_context.payload = {"cache": cache}


@when("catalog payload is exported for agent cursor")
def when_export_catalog(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["catalog"] = catalog_payload(
        gherkin_context.payload["cache"],
        agent="cursor",
    )


@then("catalog tool names should equal cache tool names exactly")
def then_names_match(gherkin_context: GherkinContext) -> None:
    cache_names = [tool["name"] for tool in gherkin_context.payload["cache"].snapshot()]
    catalog_names = [tool["name"] for tool in gherkin_context.payload["catalog"]["tools"]]
    assert catalog_names == cache_names


@given("mcp server configs with one valid and one invalid backend")
def given_mixed_backends(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "mcp_servers": {
            "good": {"command": "echo", "args": ["ok"]},
            "broken_backend": None,
        },
    }


@when("backend servers are mounted on the aggregator")
def when_mount_backends(gherkin_context: GherkinContext) -> None:
    config = _sample_config(mcp_servers=gherkin_context.payload["mcp_servers"])
    cache = RuntimeToolCache()

    def _fail_broken(_server: FastMCP, mcp_servers: dict[str, Any]) -> list[str]:
        degraded: list[str] = []
        for name in mcp_servers:
            if mcp_servers[name] is None:
                degraded.append(str(name))
        return degraded

    with patch("cyt_mcp.aggregator.mount_backend_servers", side_effect=_fail_broken):
        gherkin_context.payload["server"] = build_aggregator(config, cache)
        gherkin_context.payload["degraded"] = _fail_broken(
            gherkin_context.payload["server"],
            gherkin_context.payload["mcp_servers"],
        )


@then(parsers.parse("degraded servers should include {name}"))
def then_degraded_includes(name: str, gherkin_context: GherkinContext) -> None:
    assert name in gherkin_context.payload["degraded"]


@then("the aggregator server should still be constructed")
def then_server_built(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["server"] is not None

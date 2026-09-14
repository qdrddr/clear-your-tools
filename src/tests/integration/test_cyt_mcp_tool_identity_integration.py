"""Integration tests: backend catalog enrichment → Type-2 session log → preToolUse gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP
from fastmcp.tools.base import Tool

from cyt.injection.session_log_build import build_tool_catalog_log_entry
from cyt.injection.tool_catalog_emit import emit_tool_catalog_session_log
from cyt_client.tool_gate import validate_pre_tool_call
from cyt_mcp.catalog import catalog_payload
from cyt_mcp.catalog_build import build_catalog_from_tools, refresh_catalog_cache
from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.tool_identity import resolve_backend_identity
from tests.support.cyt_mcp_tool_identity_fixtures import (
    GateScenario,
    backend_tool_to_type2_record,
    enrich_backend_tools,
    load_backend_tools,
    load_gate_scenarios,
    load_server_keys,
    patch_session_log_resolver,
    write_type2_session_log,
)


def _backend_tools_as_fastmcp() -> list[Tool]:
    tools: list[Tool] = []
    for raw in load_backend_tools():
        name = str(raw.get("name") or "")
        schema = raw.get("inputSchema") or {}
        properties = schema.get("properties") if isinstance(schema, dict) else {}
        required = schema.get("required") if isinstance(schema, dict) else []

        def _handler(**kwargs: object) -> dict[str, object]:
            return dict(kwargs)

        _handler.__name__ = name
        tool = Tool.from_function(
            _handler,
            name=name,
            description=str(raw.get("description") or ""),
        )
        mcp_tool = tool.to_mcp_tool()
        if isinstance(properties, dict):
            mcp_tool.inputSchema = {
                "type": "object",
                "properties": properties,
                **({"required": required} if isinstance(required, list) and required else {}),
            }
        tools.append(tool)
    return tools


@pytest.mark.integration
def test_build_catalog_from_tools_then_enrich_matches_fixture_identities() -> None:
    server_keys = load_server_keys()
    backend_tools = _backend_tools_as_fastmcp()
    catalog_entries, _search_index = build_catalog_from_tools(backend_tools)
    enriched = enrich_backend_tools(catalog_entries, server_keys)

    by_name = {str(tool["name"]): tool for tool in enriched}
    graphify = by_name["graphify_query_graph"]
    codebase = by_name["codebase-memory_query_graph"]
    assert graphify["server_key"] == "graphify"
    assert graphify["tool_name"] == "query_graph"
    assert codebase["server_key"] == "codebase-memory"
    assert codebase["tool_name"] == "query_graph"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_catalog_cache_populates_explicit_identity_fields(tmp_path: Path) -> None:
    server_keys = load_server_keys()
    server_map = {key: {"command": "echo", "args": [key]} for key in server_keys}
    config = sample_aggregator_config(
        mcp_servers=server_map,
        workspace_root=tmp_path,
    )

    server = FastMCP("cyt-mcp-test")
    backend_tools = _backend_tools_as_fastmcp()

    async def _list_tools(_self: FastMCP) -> list[Tool]:
        return backend_tools

    backend_server: Any = server
    backend_server._list_tools = _list_tools.__get__(server, FastMCP)

    cache = RuntimeToolCache()
    await refresh_catalog_cache(server, cache, config)

    snapshot = cache.snapshot()
    graphify = next(item for item in snapshot if item.get("name") == "graphify_query_graph")
    assert graphify.get("server_key") == "graphify"
    assert graphify.get("tool_name") == "query_graph"

    payload = catalog_payload(
        cache,
        agent="cursor",
        server_keys=list(server_keys),
    )
    exported = payload["tools"]
    exported_graphify = next(
        item for item in exported if item.get("name") == "graphify_query_graph"
    )
    assert exported_graphify.get("server_key") == "graphify"
    assert exported_graphify.get("tool_name") == "query_graph"


@pytest.mark.integration
def test_emit_tool_catalog_session_log_round_trip_from_enriched_catalog() -> None:
    server_keys = load_server_keys()
    enriched = enrich_backend_tools(load_backend_tools(), server_keys)
    catalog = [
        {
            **backend_tool_to_type2_record(tool),
            "cyt_catalog_source": "cyt_mcp",
        }
        for tool in enriched
    ]
    entries = emit_tool_catalog_session_log(catalog, payload={}, tools_inject_enabled=True)
    catalog_entries = [entry for entry in entries if entry.get("kind") == "tool_catalog"]
    assert len(catalog_entries) == 1
    tools = catalog_entries[0]["tools"]
    names = {str(tool["name"]) for tool in tools}
    assert "graphify_query_graph" in names
    assert "codebase-memory_query_graph" in names
    for tool in tools:
        assert tool.get("server_key")
        assert tool.get("tool_name")


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [item for item in load_gate_scenarios() if item.catalog == "wire"],
    ids=lambda item: item.id,
)
def test_e2e_enriched_catalog_to_gate_from_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: GateScenario,
) -> None:
    server_keys = load_server_keys()
    enriched = enrich_backend_tools(load_backend_tools(), server_keys)
    type2_tools = [backend_tool_to_type2_record(tool) for tool in enriched]
    build_tool_catalog_log_entry("cyt_mcp", type2_tools)

    log_path = tmp_path / "session.jsonl"
    write_type2_session_log(log_path, type2_tools)
    patch_session_log_resolver(monkeypatch, log_path)

    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": scenario.tool_name,
            "tool_input": scenario.tool_input,
            "workspace_roots": ["/tmp/clear-your-tools"],
        },
    )
    assert validation.allowed is scenario.allowed, validation.reason
    for fragment in scenario.reason_contains:
        assert fragment in validation.reason


@pytest.mark.integration
def test_tool_use_feedback_resolves_backend_from_explicit_catalog_fields() -> None:
    server_keys = load_server_keys()
    enriched = enrich_backend_tools(load_backend_tools(), server_keys)
    graphify = next(item for item in enriched if item.get("name") == "graphify_query_graph")
    server, bare = resolve_backend_identity(graphify)
    assert server == "graphify"
    assert bare == "query_graph"


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [item for item in load_gate_scenarios() if item.catalog == "bare_regression"],
    ids=lambda item: item.id,
)
def test_legacy_bare_catalog_on_disk_does_not_apply_wrong_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: GateScenario,
) -> None:
    from tests.support.cyt_mcp_tool_identity_fixtures import (
        bare_regression_type2_catalog,
        write_type2_session_log,
    )

    log_path = tmp_path / f"legacy-{scenario.id}.jsonl"
    write_type2_session_log(
        log_path,
        bare_regression_type2_catalog(),
        legacy_bare_catalog=True,
    )
    patch_session_log_resolver(monkeypatch, log_path)

    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": scenario.tool_name,
            "tool_input": scenario.tool_input,
            "workspace_roots": ["/tmp/clear-your-tools"],
        },
    )
    assert validation.allowed is scenario.allowed, validation.reason
    for fragment in scenario.reason_must_not_contain:
        assert fragment not in validation.reason


@pytest.mark.integration
def test_session_log_json_fixture_is_valid_type2_bundle(tmp_path: Path) -> None:
    server_keys = load_server_keys()
    enriched = enrich_backend_tools(load_backend_tools(), server_keys)
    type2_tools = [backend_tool_to_type2_record(tool) for tool in enriched]
    log_path = tmp_path / "session.jsonl"
    write_type2_session_log(log_path, type2_tools)

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    catalog_entry = json.loads(lines[1])
    assert catalog_entry["kind"] == "tool_catalog"
    assert catalog_entry["catalog"] == "cyt_mcp"
    assert len(catalog_entry["tools"]) == len(type2_tools)

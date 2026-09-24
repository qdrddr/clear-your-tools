"""Gherkin steps for cyt-mcp catalog resilience (reload, restart, injection)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP
from fastmcp.tools.base import Tool
from mcp.types import ListToolsRequest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.hook.catalog_registry import catalog_for_hook, clear_catalog_registry
from cyt.skills.cli import run_hook_payload
from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
from cyt.tools.master_catalog import clear_master_catalog_cache, get_master_tool_catalog
from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME, register_search_tool
from cyt_mcp.session_runtime import MultiWorkspaceCoordinator, WorkspaceSessionRuntime
from cyt_mcp.session_workspace_middleware import SessionWorkspaceMiddleware
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    capture_registry_registrations,
    cyt_mcp_hook_config,
    load_resilience_scenario,
    load_ws_tools_catalog,
    materialize_workspace,
    patch_daemon_catalog_status,
    patch_tiers_stats_config,
    register_dual_layer_catalog,
    register_ws_catalog,
    reset_catalog_state,
    write_usr_scope_disk_catalog,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = (
    Path(__file__).resolve().parent / "features" / "cyt_mcp_catalog_resilience.feature"
)
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@pytest.fixture(autouse=True)
def _isolate_catalog_state(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setattr("cyt.hook.active_workspace.touch_active_workspace", lambda *_a, **_k: None)
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")
    reset_catalog_state()
    yield
    reset_catalog_state()


def _middleware_for_scope(
    *,
    catalog_scope: str,
    workspace: Path,
    cache: RuntimeToolCache,
) -> tuple[SessionWorkspaceMiddleware, AsyncMock]:
    config = sample_aggregator_config(
        catalog_scope="workspace" if catalog_scope == "workspace" else "user",
        workspace_root=workspace,
    )
    server_name = "cyt-mcp-ws" if catalog_scope == "workspace" else "cyt-mcp-usr"
    server = FastMCP(server_name)
    register_search_tool(server, cache, agent=config.agent)
    bootstrap = WorkspaceSessionRuntime(
        workspace_root=workspace,
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
    return middleware, call_next


@given("a user-scoped cyt-mcp session with an empty runtime cache")
def given_user_empty_cache(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    workspace = materialize_workspace(tmp_path)
    cache = RuntimeToolCache()
    middleware, call_next = _middleware_for_scope(
        catalog_scope="user",
        workspace=workspace,
        cache=cache,
    )
    gherkin_context.payload = {
        "middleware": middleware,
        "call_next": call_next,
        "scenario": load_resilience_scenario("usr_reload_empty_cache"),
    }


@given("a workspace-scoped cyt-mcp session with a populated runtime cache")
def given_workspace_populated_cache(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    workspace = materialize_workspace(tmp_path)
    cache = RuntimeToolCache()
    cache.replace(
        [
            {"name": tool["name"], "inputSchema": tool["input_schema"]}
            for tool in load_ws_tools_catalog()
        ],
    )
    middleware, call_next = _middleware_for_scope(
        catalog_scope="workspace",
        workspace=workspace,
        cache=cache,
    )
    gherkin_context.payload = {
        "middleware": middleware,
        "call_next": call_next,
        "scenario": load_resilience_scenario("ws_reload_populated_cache"),
    }


@given("a workspace cyt-mcp catalog registered in the hook daemon")
def given_registered_ws_catalog(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    workspace = materialize_workspace(tmp_path)
    register_ws_catalog(workspace, load_ws_tools_catalog())
    gherkin_context.payload["workspace"] = workspace


@given("the in-memory catalog registry was cleared like a CLI cold start")
def given_registry_cleared(gherkin_context: GherkinContext, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = capture_registry_registrations()
    clear_catalog_registry(purge_disk_snapshot=False)
    clear_master_catalog_cache()
    patch_daemon_catalog_status(monkeypatch, captured)
    gherkin_context.payload["daemon_registrations"] = captured


@given("a cyt-mcp hook config with BM25 pruning enabled")
def given_hook_config(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    workspace = gherkin_context.payload["workspace"]
    gherkin_context.config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")
    gherkin_context.payload["inject_scenario"] = load_resilience_scenario(
        "hook_inject_bm25_prompt",
    )


@given("dual-layer usr and ws cyt-mcp catalogs registered for the workspace")
def given_dual_layer_catalog(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    workspace = materialize_workspace(tmp_path)
    ws_tools, usr_tools = register_dual_layer_catalog(workspace)
    gherkin_context.payload["workspace"] = workspace
    gherkin_context.payload["ws_tools"] = ws_tools
    gherkin_context.payload["usr_tools"] = usr_tools
    gherkin_context.payload["union_scenario"] = load_resilience_scenario(
        "usr_ws_union_tiers_stats",
    )


@given("a cyt-mcp hook config with tier tracking enabled")
def given_tier_tracking_config(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = gherkin_context.payload["workspace"]
    gherkin_context.config = patch_tiers_stats_config(
        monkeypatch,
        workspace,
        db_path=tmp_path / "tiers.db",
    )
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())


@given("user-scoped cyt-mcp tools cached on disk")
def given_usr_disk_catalog(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gherkin_context.payload["usr_disk_tools"] = write_usr_scope_disk_catalog(
        monkeypatch,
        tmp_path / "cyt-mcp-catalog",
    )
    gherkin_context.payload["disk_merge_scenario"] = load_resilience_scenario(
        "usr_disk_merge_ws_registry",
    )


@when("tools/list is requested through session workspace middleware")
def when_tools_list(gherkin_context: GherkinContext) -> None:
    middleware: SessionWorkspaceMiddleware = gherkin_context.payload["middleware"]
    call_next = gherkin_context.payload["call_next"]
    context = MagicMock()
    context.method = "tools/list"
    context.message = ListToolsRequest(method="tools/list")
    context.fastmcp_context = None
    with patch.object(middleware._coordinator, "ensure_backends_mounted"):
        gherkin_context.payload["tools"] = asyncio.run(middleware.on_list_tools(context, call_next))


@when("catalog registry hydration runs for read")
def when_registry_hydrate() -> None:
    from cyt.hook.catalog_registry import hydrate_catalog_registry_for_read

    hydrate_catalog_registry_for_read()


@when("master catalog is loaded blocking for the workspace")
def when_master_catalog_blocking(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = gherkin_context.payload["workspace"]
    config = cyt_mcp_hook_config(workspace, db_path=tmp_path / "tiers.db")

    if "disk_merge_scenario" in gherkin_context.payload:
        patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
        clear_master_catalog_cache()
        clear_cyt_mcp_catalog_cache()
        write_usr_scope_disk_catalog(
            monkeypatch,
            tmp_path / "cyt-mcp-catalog",
            usr_tools=gherkin_context.payload.get("usr_disk_tools"),
        )
        gherkin_context.payload["master_catalog"] = get_master_tool_catalog(config, blocking=True)
        return

    if "daemon_registrations" in gherkin_context.payload:
        clear_master_catalog_cache()
        clear_cyt_mcp_catalog_cache()

    gherkin_context.payload["master_catalog"] = get_master_tool_catalog(config, blocking=True)
    gherkin_context.payload["hydrate_scenario"] = load_resilience_scenario(
        "daemon_restart_registry_hydrate",
    )


@when("cyt tiers stats runs with JSON output for the workspace")
def when_tiers_stats_json(
    gherkin_context: GherkinContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cyt.tiers.cli import main as tiers_main

    workspace = gherkin_context.payload["workspace"]
    code = tiers_main(["stats", "--workspace", str(workspace), "--json"])
    assert code == 0
    import json

    gherkin_context.payload["tiers_stats_json"] = json.loads(capsys.readouterr().out)


@when("cyt tiers stats runs with verbose text output")
def when_tiers_stats_verbose(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cyt.tiers.cli import main as tiers_main

    workspace = gherkin_context.payload["workspace"]
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")
    code = tiers_main(["stats", "--workspace", str(workspace), "--verbose"])
    assert code == 0
    gherkin_context.payload["tiers_stats_text"] = capsys.readouterr().out


@when("hook inject runs for the catalog resilience BM25 prompt")
def when_hook_inject(gherkin_context: GherkinContext) -> None:
    scenario = gherkin_context.payload["inject_scenario"]
    workspace = gherkin_context.payload["workspace"]
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": scenario.raw["prompt"],
        "cwd": str(workspace),
        "workspace_roots": [str(workspace)],
        "model": "claude-sonnet-4-20250514",
    }
    gherkin_context.payload["hook_result"] = run_hook_payload(payload, gherkin_context.config)


@then("tools/list should fall back to registered MCP tools")
def then_tools_list_fallback(gherkin_context: GherkinContext) -> None:
    scenario = gherkin_context.payload["scenario"]
    tools = gherkin_context.payload["tools"]
    call_next = gherkin_context.payload["call_next"]
    assert len(tools) >= int(scenario.raw["expected_min_tools"])
    assert any(tool.to_mcp_tool().name == scenario.raw["expected_wire_tool"] for tool in tools)
    call_next.assert_awaited_once()


@then("tools/list should include workspace backend stubs")
def then_tools_list_stubs(gherkin_context: GherkinContext) -> None:
    scenario = gherkin_context.payload["scenario"]
    tools = gherkin_context.payload["tools"]
    call_next = gherkin_context.payload["call_next"]
    names = {tool.to_mcp_tool().name for tool in tools}
    for expected in scenario.raw["expected_tool_names"]:
        assert expected in names
    call_next.assert_not_awaited()


@then("catalog_for_hook should expose the registered workspace tools")
def then_catalog_for_hook(gherkin_context: GherkinContext) -> None:
    workspace = gherkin_context.payload["workspace"]
    scenario = load_resilience_scenario("daemon_restart_registry_hydrate")
    merged = catalog_for_hook("cursor", workspace, allow_stale=False)
    assert {tool["name"] for tool in merged} == set(scenario.raw["expected_tool_names"])


@then("master catalog tool count should meet the resilience scenario minimum")
def then_master_catalog_minimum(gherkin_context: GherkinContext) -> None:
    scenario = gherkin_context.payload["hydrate_scenario"]
    catalog = gherkin_context.payload["master_catalog"]
    assert catalog is not None
    assert len(catalog) >= int(scenario.raw["minimum_master_catalog_tools"])


@then("hook stdout should include expected cyt-mcp tool names")
def then_hook_stdout_tools(gherkin_context: GherkinContext) -> None:
    scenario = gherkin_context.payload["inject_scenario"]
    result = gherkin_context.payload["hook_result"]
    assert result.outcome not in {
        "skipped_cyt_mcp_unavailable",
        "skipped_missing_tools_catalog",
        "user_prompt_no_tool_matches",
    }
    stdout = result.stdout_text
    for name in scenario.raw["expected_tool_names_in_stdout"]:
        assert name in stdout


@then("tiers stats catalog count should equal usr plus ws tool totals")
def then_tiers_stats_union_count(gherkin_context: GherkinContext) -> None:
    scenario = gherkin_context.payload["union_scenario"]
    payload = gherkin_context.payload["tiers_stats_json"]
    troubleshooting = payload["overview"]["troubleshooting"]
    tier_total = payload["overview"]["tier_statistics"]["tools"]["totals"]["count"]
    expected_total = int(scenario.raw["expected_total_tools"])

    assert troubleshooting["catalog_tool_count"] == expected_total
    assert troubleshooting["catalog_user_tool_count"] == scenario.raw["expected_user_tool_count"]
    assert troubleshooting["catalog_workspace_tool_count"] == scenario.raw[
        "expected_workspace_tool_count"
    ]
    assert tier_total == expected_total


@then("tiers stats scope should default to all")
def then_tiers_stats_scope_all(gherkin_context: GherkinContext) -> None:
    scenario = gherkin_context.payload["union_scenario"]
    payload = gherkin_context.payload["tiers_stats_json"]
    assert payload.get("scope") == scenario.raw["expected_scope"]


@then("master catalog should include both workspace and user tool names")
def then_master_catalog_includes_disk_merged_usr(gherkin_context: GherkinContext) -> None:
    scenario = gherkin_context.payload["disk_merge_scenario"]
    catalog = gherkin_context.payload["master_catalog"]
    assert catalog is not None
    names = {tool["name"] for tool in catalog}
    assert names == set(scenario.raw["expected_merged_tool_names"])


@then("troubleshooting should show user and workspace catalog counts")
def then_troubleshooting_scope_breakdown(gherkin_context: GherkinContext) -> None:
    scenario = gherkin_context.payload["union_scenario"]
    text = gherkin_context.payload["tiers_stats_text"]
    assert f"user={scenario.raw['expected_user_tool_count']}" in text
    assert f"workspace={scenario.raw['expected_workspace_tool_count']}" in text
    assert "get-tool-definitions" in text

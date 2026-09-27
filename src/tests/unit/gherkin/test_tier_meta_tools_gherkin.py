"""Gherkin steps for cyt-mcp meta tool tier exclusion."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.hook.catalog_registry import catalog_for_hook, clear_catalog_registry
from cyt.tiers.adapters.tools import filter_tools_for_tier_tracking
from cyt.tiers.models import EntityKind
from cyt.tools.master_catalog import clear_master_catalog_cache, get_master_tool_catalog
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME, SEARCH_TOOL_NAME
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    capture_registry_registrations,
    load_usr_tools_catalog,
    load_ws_tools_catalog,
    materialize_workspace,
    patch_daemon_catalog_status,
    patch_tiers_stats_config,
    reset_catalog_state,
)
from tests.support.tier_capture_fixtures import (
    load_catalog_layer_registration_scenarios,
    load_meta_tools_not_reported,
    materialize_capture_pack,
    meta_tool_entity_id,
    meta_tool_tier_feedback_by_id,
    post_tier_feedback_http,
    register_catalog_with_meta_tools,
    register_dual_layer_catalog_with_meta_tools,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "tier_meta_tools.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given(parsers.parse("a workspace with cyt-mcp {layer} catalog registration including meta tools"))
def given_layer_registration_with_meta_tools(
    layer: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    clear_catalog_registry()
    scenario = next(
        item for item in load_catalog_layer_registration_scenarios() if item.catalog_layer == layer
    )
    pack = materialize_capture_pack(tmp_path)
    register_catalog_with_meta_tools(
        pack.workspace,
        catalog_layer=scenario.catalog_layer,
        backend_tool_name=scenario.backend_tool_name,
        meta_tool_names=scenario.meta_tool_names,
        instance_id=f"pid:gherkin-{scenario.id}",
    )
    gherkin_context.payload = {
        "pack": pack,
        "backend_tool": scenario.backend_tool_name,
    }


@when("hook catalog is merged for cyt-mcp injection")
def when_hook_catalog_merged(gherkin_context: GherkinContext) -> None:
    pack = gherkin_context.payload["pack"]
    gherkin_context.payload["merged"] = catalog_for_hook("cursor", pack.workspace)


@then(parsers.parse("merged catalog should include backend tool {backend_tool}"))
def then_merged_includes_backend(backend_tool: str, gherkin_context: GherkinContext) -> None:
    merged = gherkin_context.payload["merged"]
    names = {str(tool.get("name") or "") for tool in merged}
    assert backend_tool in names


@then("merged catalog should exclude get-tool-definitions wire name")
def then_merged_excludes_meta_wire(gherkin_context: GherkinContext) -> None:
    merged = gherkin_context.payload["merged"]
    names = {str(tool.get("name") or "") for tool in merged}
    assert MCP_WIRE_SEARCH_TOOL_NAME not in names
    assert SEARCH_TOOL_NAME not in names


@given("cyt-mcp tier capture meta tools fixture")
def given_meta_tools_fixture(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["meta_tools"] = load_meta_tools_not_reported()


@when("tools are filtered for tier tracking with a backend tool present")
def when_filter_for_tier_tracking(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    pack = materialize_capture_pack(tmp_path)
    meta_tools = gherkin_context.payload["meta_tools"]
    tools = [{"name": "semble_search", "cyt_catalog_source": "cyt_mcp"}]
    tools.extend({"name": name, "cyt_catalog_source": "cyt_mcp"} for name in meta_tools)
    gherkin_context.payload["tracked"] = filter_tools_for_tier_tracking(tools, pack.config)


@then("tier tracking should include only backend tools")
def then_tracking_backend_only(gherkin_context: GherkinContext) -> None:
    tracked = gherkin_context.payload["tracked"]
    names = {str(tool.get("name") or "") for tool in tracked}
    assert names == {"semble_search"}


@given("a workspace with cyt-mcp tier tracking enabled")
def given_tier_tracking_workspace(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    gherkin_context.payload["pack"] = materialize_capture_pack(tmp_path)


@when("tier feedback HTTP is posted for get-tool-definitions")
def when_post_meta_tool_feedback(gherkin_context: GherkinContext) -> None:
    pack = gherkin_context.payload["pack"]
    payload = meta_tool_tier_feedback_by_id("wire_get_tool_definitions").payload

    async def _post() -> int:
        return await post_tier_feedback_http(pack, payload)

    gherkin_context.payload["http_status"] = asyncio.run(_post())


@then("tier state should not contain cyt_mcp get-tool-definitions entity")
def then_no_meta_tier_entity(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["http_status"] == 204
    pack = gherkin_context.payload["pack"]
    from cyt.tiers.manager import get_tier_manager

    manager = get_tier_manager(pack.config, workspace=pack.workspace)
    entity_id = meta_tool_entity_id(MCP_WIRE_SEARCH_TOOL_NAME)
    assert manager._states.get((EntityKind.TOOL, entity_id)) is None


@given("a workspace with usr and ws cyt-mcp catalog layers registered")
def given_usr_ws_layers(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_catalog_state()
    workspace = materialize_workspace(tmp_path)
    register_dual_layer_catalog_with_meta_tools(workspace)
    config = patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    gherkin_context.payload = {
        "workspace": workspace,
        "config": config,
        "expected_backend_count": len(load_ws_tools_catalog()) + len(load_usr_tools_catalog()),
    }


@when("master hook catalog is rebuilt for tier tracking")
def when_rebuild_master_catalog(gherkin_context: GherkinContext) -> None:
    config = gherkin_context.payload["config"]
    clear_master_catalog_cache()
    gherkin_context.payload["master"] = get_master_tool_catalog(config, blocking=True) or []


@then("master catalog should exclude get-tool-definitions and canonical meta name")
def then_master_excludes_meta(gherkin_context: GherkinContext) -> None:
    master = gherkin_context.payload["master"]
    names = {str(tool.get("name") or "") for tool in master}
    assert MCP_WIRE_SEARCH_TOOL_NAME not in names
    assert SEARCH_TOOL_NAME not in names
    assert len(names) >= int(gherkin_context.payload["expected_backend_count"])

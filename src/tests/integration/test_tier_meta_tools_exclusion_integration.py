"""Integration tests: meta tools must not reach tier manager via cyt-mcp usr/ws paths."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.tiers.adapters.tools import resolve_tracked_catalog_entity_ids
from cyt.tiers.manager import _managers
from cyt.tiers.models import EntityKind
from cyt.tools.master_catalog import clear_master_catalog_cache, get_master_tool_catalog
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
    MetaToolIntegrationScenario,
    TierCaptureFixturePack,
    load_meta_tool_integration_scenarios,
    load_meta_tool_tier_feedback_payloads,
    materialize_capture_pack,
    meta_tool_entity_id,
    meta_tool_tier_feedback_by_id,
    post_tier_feedback_http,
    register_dual_layer_catalog_with_meta_tools,
)


@pytest.fixture
def capture_pack(tmp_path: Path) -> TierCaptureFixturePack:
    return materialize_capture_pack(tmp_path)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture(autouse=True)
def reset_catalog_layers(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("cyt.hook.active_workspace.touch_active_workspace", lambda *_a, **_k: None)
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")
    reset_catalog_state()
    yield
    reset_catalog_state()


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [item for item in load_meta_tool_integration_scenarios() if item.payload_ids],
    ids=lambda item: item.id,
)
@pytest.mark.asyncio
async def test_meta_tool_http_feedback_does_not_create_tier_rows(
    capture_pack: TierCaptureFixturePack,
    scenario: MetaToolIntegrationScenario,
) -> None:
    for payload_id in scenario.payload_ids:
        payload = meta_tool_tier_feedback_by_id(payload_id).payload
        status = await post_tier_feedback_http(capture_pack, payload)
        assert status == 204

    from cyt.tiers.manager import get_tier_manager

    manager = get_tier_manager(capture_pack.config, workspace=capture_pack.workspace)
    for entity_id in scenario.forbidden_entity_ids:
        assert manager._states.get((EntityKind.TOOL, entity_id)) is None

    for feedback_scenario in load_meta_tool_tier_feedback_payloads():
        if feedback_scenario.id in scenario.payload_ids:
            assert (
                manager._states.get(
                    (EntityKind.TOOL, meta_tool_entity_id(feedback_scenario.tool_name)),
                )
                is None
            )


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [
        item
        for item in load_meta_tool_integration_scenarios()
        if item.minimum_backend_tools is not None
    ],
    ids=lambda item: item.id,
)
def test_usr_ws_union_master_catalog_excludes_meta_tools(
    tmp_path: Path,
    scenario: MetaToolIntegrationScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = materialize_workspace(tmp_path)
    register_dual_layer_catalog_with_meta_tools(workspace)
    expected_backend_count = len(load_ws_tools_catalog()) + len(load_usr_tools_catalog())

    config = patch_tiers_stats_config(monkeypatch, workspace, db_path=tmp_path / "tiers.db")
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    clear_master_catalog_cache()

    master = get_master_tool_catalog(config, blocking=True) or []
    master_names = {str(tool.get("name") or "") for tool in master}
    minimum = int(scenario.minimum_backend_tools or expected_backend_count)
    assert len(master_names) >= minimum
    for forbidden in scenario.forbidden_names:
        assert forbidden not in master_names

    tracked_ids = resolve_tracked_catalog_entity_ids(config, blocking=True) or frozenset()
    for forbidden in scenario.forbidden_names:
        assert meta_tool_entity_id(forbidden) not in tracked_ids

"""Integration tests: permissions disable propagates to catalog filter and tier manager."""

from __future__ import annotations

import httpx
import pytest

from cyt.cyt_mcp.catalog import _filter_tools_by_permissions, apply_fetched_catalog
from cyt.hook.permissions_revision import clear_permissions_revisions, get_permissions_revision
from cyt.tiers.adapters.tools import filter_tools_for_tier_tracking
from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.models import Tier
from cyt.tools.master_catalog import rebuild_master_catalog
from tests.support.permissions_propagation_fixtures import (
    PropagationFixturePack,
    PropagationScenario,
    assert_catalog_and_detail_filters_agree,
    disable_tool_on_pack,
    enable_tool_on_pack,
    load_propagation_scenarios,
    notify_hook_permissions_changed,
    patch_global_config_path,
    seed_catalog_on_hook,
    tier_config_for_pack,
    tool_entity_ids_in_status,
)
from tests.support.tier_seed_helpers import seed_tool_tiers


@pytest.fixture
def clear_tier_managers() -> None:
    _managers.clear()


@pytest.mark.integration
@pytest.mark.parametrize("scenario", load_propagation_scenarios(), ids=lambda s: s.id)
@pytest.mark.asyncio
async def test_permissions_disable_propagates_to_catalog_and_tier_tracking(
    propagation_pack: PropagationFixturePack,
    scenario: PropagationScenario,
    propagation_hook_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    clear_tier_managers: None,
) -> None:
    patch_global_config_path(monkeypatch, propagation_pack)
    clear_permissions_revisions()
    config = tier_config_for_pack(propagation_pack)
    catalog_tools = seed_catalog_on_hook(propagation_pack, config)

    denied_name = scenario.denied_catalog_name
    enabled_names = set(scenario.enabled_catalog_names)
    db_path = propagation_pack.workspace / ".agents" / "cyt" / "tier_state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    seed_tool_tiers(
        workspace=propagation_pack.workspace,
        db_path=db_path,
        tier_map={f"cyt_mcp:{denied_name}": Tier.HOT},
    )

    disable_tool_on_pack(propagation_pack, scenario.disable_target)
    revision = await notify_hook_permissions_changed(
        propagation_hook_client,
        workspace_root=propagation_pack.workspace,
    )
    assert revision == 1
    assert get_permissions_revision("cursor", propagation_pack.workspace) == 1

    assert_catalog_and_detail_filters_agree(
        config=config,
        pack=propagation_pack,
        enabled_names=enabled_names,
        denied_name=denied_name,
    )

    rebuild_master_catalog(config, blocking=True)
    tracked_names = {
        str(tool["name"]) for tool in filter_tools_for_tier_tracking(catalog_tools, config)
    }
    assert denied_name not in tracked_names

    manager = TierManager(propagation_pack.workspace, str(db_path))
    try:
        status = manager.status(config, agent="cursor", filter_by_permissions=True)
        assert f"cyt_mcp:{denied_name}" not in tool_entity_ids_in_status(status)
    finally:
        manager.close()

    enable_tool_on_pack(propagation_pack, scenario.disable_target)
    await notify_hook_permissions_changed(
        propagation_hook_client,
        workspace_root=propagation_pack.workspace,
    )
    apply_fetched_catalog(config, catalog_tools)
    rebuild_master_catalog(config, blocking=True)

    restored_names = {
        str(tool["name"]) for tool in _filter_tools_by_permissions(config, catalog_tools)
    }
    assert denied_name in restored_names
    assert restored_names == set(propagation_pack.catalog_tool_names)

    restored_tracked_names = {
        str(tool["name"]) for tool in filter_tools_for_tier_tracking(catalog_tools, config)
    }
    assert denied_name in restored_tracked_names

    assert_catalog_and_detail_filters_agree(
        config=config,
        pack=propagation_pack,
        enabled_names=set(propagation_pack.catalog_tool_names),
    )

    manager = TierManager(propagation_pack.workspace, str(db_path))
    try:
        restored_status = manager.status(config, agent="cursor", filter_by_permissions=True)
        restored_ids = tool_entity_ids_in_status(restored_status)
        assert f"cyt_mcp:{denied_name}" in restored_ids or denied_name in {
            str(row.get("display_name") or "")
            for rows in restored_status.get("tools", {}).get("by_tier", {}).values()
            if isinstance(rows, list)
            for row in rows
            if isinstance(row, dict)
        }
    finally:
        manager.close()

"""Unit tests for permissions propagation fixtures and tier-tracking defense."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from cyt.cyt_mcp.catalog import (
    _catalog_states,
    _CytMcpCacheKey,
    _CytMcpCatalogState,
    invalidate_cyt_mcp_catalog_for_workspace,
)
from cyt.permissions.editor import load_permissions_lists
from cyt.tiers.adapters.tools import filter_tools_for_tier_tracking
from tests.support.permissions_propagation_fixtures import (
    CATALOG_TOOLS_PATH,
    SCENARIOS_PATH,
    PropagationFixturePack,
    PropagationScenario,
    disable_tool_on_pack,
    load_catalog_tools,
    load_propagation_scenarios,
    materialize_propagation_fixture_pack,
    patch_global_config_path,
    seed_catalog_on_hook,
    tier_config_for_pack,
)


@pytest.fixture
def propagation_pack(tmp_path: Path) -> PropagationFixturePack:
    return materialize_propagation_fixture_pack(tmp_path)


def test_propagation_fixture_files_exist() -> None:
    assert SCENARIOS_PATH.is_file()
    assert CATALOG_TOOLS_PATH.is_file()
    names, tools = load_catalog_tools()
    scenarios = load_propagation_scenarios()
    assert "hedl_batch" in names
    assert len(tools) == len(names)
    assert len(scenarios) >= 3


@pytest.mark.parametrize("scenario", load_propagation_scenarios(), ids=lambda s: s.id)
def test_filter_tools_for_tier_tracking_skips_denied_tool(
    propagation_pack: PropagationFixturePack,
    scenario: PropagationScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_global_config_path(monkeypatch, propagation_pack)
    config = tier_config_for_pack(propagation_pack)
    catalog_tools = seed_catalog_on_hook(propagation_pack, config)
    disable_tool_on_pack(propagation_pack, scenario.disable_target)

    tracked_names = {
        str(tool["name"]) for tool in filter_tools_for_tier_tracking(catalog_tools, config)
    }
    assert scenario.denied_catalog_name not in tracked_names
    assert tracked_names == set(scenario.enabled_catalog_names)


def test_editor_disable_writes_workspace_deny_entry(
    propagation_pack: PropagationFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_global_config_path(monkeypatch, propagation_pack)
    scenario = load_propagation_scenarios()[0]
    disable_tool_on_pack(propagation_pack, scenario.disable_target)

    raw = yaml.safe_load(propagation_pack.workspace_config_path.read_text(encoding="utf-8")) or {}
    deny, _allow = load_permissions_lists(raw, kind="mcp", agent_target="all")
    assert deny, "expected workspace deny entry after disable"
    assert any("hedl" in entry and "batch" in entry for entry in deny)


def test_invalidate_cyt_mcp_catalog_for_workspace_drops_only_target(
    tmp_path: Path,
) -> None:
    workspace_a = tmp_path / "repo_a"
    workspace_b = tmp_path / "repo_b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    (workspace_a / ".git").mkdir()
    (workspace_b / ".git").mkdir()

    key_a = _CytMcpCacheKey(agent="cursor", slug="slug-a", workspace=str(workspace_a.resolve()))
    key_b = _CytMcpCacheKey(agent="cursor", slug="slug-b", workspace=str(workspace_b.resolve()))
    _catalog_states[key_a] = _CytMcpCatalogState(tools=[{"name": "tool_a"}])
    _catalog_states[key_b] = _CytMcpCatalogState(tools=[{"name": "tool_b"}])

    with (
        patch("cyt.cyt_mcp.catalog.uses_cyt_mcp_tool_catalog", return_value=False),
        patch("cyt.tools.master_cache_scheduler.schedule_master_catalog_refresh"),
    ):
        invalidate_cyt_mcp_catalog_for_workspace("cursor", workspace_a)

    assert key_a not in _catalog_states
    assert key_b in _catalog_states
    _catalog_states.clear()

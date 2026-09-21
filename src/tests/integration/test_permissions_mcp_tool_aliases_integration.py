"""Integration tests: hedl_batch alias deny through catalog filter and cyt-mcp reload."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from cyt.cyt_mcp.catalog import _filter_tools_by_permissions
from cyt.hook.catalog_registry import catalog_for_hook, clear_catalog_registry, register_catalog
from cyt.permissions.match import is_catalog_tool_denied
from cyt.permissions.merge import merged_hook_config
from cyt_mcp.config import AggregatorConfig, sample_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.hook_daemon_push import (
    PushContext,
    _instance_key,
    _last_permissions_revision,
    _maybe_reload_permissions,
)
from cyt_mcp.runtime_cache import RuntimeToolCache
from tests.support.permissions_mcp_tool_aliases_fixtures import (
    McpToolAliasFixturePack,
    apply_alias_steps,
    load_alias_scenarios,
    load_catalog_tools,
    materialize_alias_fixture_pack,
    patch_global_config_path,
)


def _merged_config(pack: McpToolAliasFixturePack, agent: str = "cursor") -> dict:
    global_cfg = yaml.safe_load(pack.global_config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(global_cfg, dict):
        global_cfg = {}
    return merged_hook_config(agent, global_config=global_cfg, workspace_root=pack.workspace)


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario_id",
    [
        "workspace_disable_hedl_batch_via_slash_agent_alias",
        "workspace_disable_hedl_batch_via_mcp_wire_name",
        "preseed_agent_alias_deny_blocks_catalog_batch",
    ],
    ids=[
        "slash_agent_alias",
        "mcp_wire_name",
        "preseed_agent_alias",
    ],
)
def test_alias_deny_filters_hook_catalog_view(
    tmp_path: Path,
    scenario_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_alias_fixture_pack(tmp_path)
    patch_global_config_path(monkeypatch, pack)
    _, scenarios = load_alias_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    apply_alias_steps(pack, scenario)

    config = _merged_config(pack, agent=scenario.agent)
    filtered = _filter_tools_by_permissions(config, pack.tools)
    filtered_names = {str(tool["name"]) for tool in filtered}
    assert filtered_names == set(scenario.expected.enabled_tools)
    assert "hedl_batch" not in filtered_names


@pytest.mark.integration
def test_hook_registry_catalog_respects_hedl_batch_alias_deny(tmp_path: Path) -> None:
    clear_catalog_registry()
    pack = materialize_alias_fixture_pack(tmp_path)
    disable_from_scenario = next(
        item
        for item in load_alias_scenarios()[1]
        if item.id == "workspace_disable_hedl_batch_via_backend_path"
    )
    apply_alias_steps(pack, disable_from_scenario)

    tools = load_catalog_tools()
    register_catalog(
        {
            "agent": "cursor",
            "scope": "workspace",
            "workspace_root": str(pack.workspace),
            "catalog_layer": "ws",
            "instance_id": "pid:test",
            "content_hash": "abc",
            "tools": tools,
        },
    )

    raw = catalog_for_hook("cursor", pack.workspace)
    assert {tool["name"] for tool in raw} == {tool["name"] for tool in tools}

    config = _merged_config(pack)
    filtered = _filter_tools_by_permissions(config, raw)
    assert "hedl_batch" not in {tool["name"] for tool in filtered}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_maybe_reload_permissions_notifies_on_deny_only_change(tmp_path: Path) -> None:
    config = sample_aggregator_config(
        catalog_scope="workspace",
        workspace_root=tmp_path,
    )
    key = _instance_key(config)
    _last_permissions_revision.clear()

    cache = RuntimeToolCache()
    cache.replace(load_catalog_tools())

    class _TrackingConfigHolder(ConfigHolder):
        def __init__(self, agg_config: AggregatorConfig) -> None:
            super().__init__(agg_config)
            self.reload_calls = 0

        def reload_mcp_deny(self) -> tuple[str, ...]:
            self.reload_calls += 1
            self.config = replace(self.config, mcp_deny=("hedl/hedl_batch",))
            return self.config.mcp_deny

    holder = _TrackingConfigHolder(config)
    middleware = MagicMock()
    middleware.notify_all_sessions = AsyncMock()

    context = PushContext(
        config_holder=holder,
        cache=cache,
        server=MagicMock(),
        list_changed_middleware=middleware,
    )

    async def noop_refresh(
        _server: object,
        _runtime_cache: RuntimeToolCache,
        _config: object,
        *,
        skip_push: bool = False,
    ) -> None:
        del _server, _runtime_cache, _config, skip_push

    with patch("cyt_mcp.catalog_build.refresh_catalog_cache", side_effect=noop_refresh):
        await _maybe_reload_permissions(key=key, revision=1, context=context)

    assert holder.reload_calls == 1
    middleware.notify_all_sessions.assert_awaited_once()
    assert is_catalog_tool_denied("hedl_batch", holder.mcp_deny)


@pytest.mark.integration
def test_merged_hook_config_applies_alias_deny_to_catalog_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_alias_fixture_pack(tmp_path)
    patch_global_config_path(monkeypatch, pack)
    scenario = next(
        item
        for item in load_alias_scenarios()[1]
        if item.id == "workspace_disable_hedl_batch_via_agent_visible_name"
    )
    apply_alias_steps(pack, scenario)

    config = _merged_config(pack, agent=scenario.agent)
    filtered = _filter_tools_by_permissions(config, pack.tools)
    assert {tool["name"] for tool in filtered} == set(scenario.expected.enabled_tools)

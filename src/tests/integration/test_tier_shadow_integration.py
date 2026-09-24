"""Integration tests for production shadow evaluation wiring."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.tiers.adapters.skills import resolve_tiered_skill_matches
from cyt.tiers.adapters.tools import mcp_server_entity_id, stamp_tool_catalog_source
from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.models import EntityKind, Tier
from cyt.tools.master_catalog import clear_master_catalog_cache
from tests.support.tier_behavior_fixtures import (
    TierBehaviorFixturePack,
    build_registry_from_pack,
    live_tier_config,
    materialize_fixture_pack,
    patch_cyt_mcp_paths,
    seed_skill_tiers_by_doc_id,
    seed_tool_tiers,
    skill_entity_ids_by_doc_id,
    write_cyt_mcp_disk_catalog_for_pack,
)
from tests.support.tier_seed_helpers import seed_entity_tier
from tests.support.tier_transitions_fixtures import (
    load_fast_wake_prompts,
    load_mcp_server_tools,
    materialize_transitions_pack,
    tier_transitions_config,
)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def fixture_pack(tmp_path: Path) -> TierBehaviorFixturePack:
    return materialize_fixture_pack(tmp_path)


@pytest.fixture
def disk_catalog_pack(
    fixture_pack: TierBehaviorFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TierBehaviorFixturePack]:
    patch_cyt_mcp_paths(monkeypatch, fixture_pack)
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()
    write_cyt_mcp_disk_catalog_for_pack(fixture_pack)
    yield fixture_pack
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()


def _sync_shadow_submit(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    submitted: list[bool] = []

    def _submit(fn: Callable[[], None]) -> None:
        submitted.append(True)
        fn()

    monkeypatch.setattr("cyt.tiers.shadow._executor.submit", _submit)
    return submitted


@pytest.mark.integration
def test_filter_tools_for_query_wakes_dormant_tool_via_shadow(
    disk_catalog_pack: TierBehaviorFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = disk_catalog_pack
    entity_id = "cyt_mcp:gitnexus_query"
    seed_tool_tiers(pack, {entity_id: Tier.DORMANT})
    config = live_tier_config(pack, kind="tool")
    manager = TierManager(pack.workspace, str(pack.db_path))
    submitted = _sync_shadow_submit(monkeypatch)

    try:
        manager.begin_request_cycle(config)
        with patch("cyt.tiers.manager.get_tier_manager_for_config", return_value=manager):
            filter_tools_for_query(
                pack.tools,
                "please run gitnexus_query on the codebase",
                ["bm25"],
                config=config,
                for_hook=True,
            )
        manager.flush_pending()
        assert submitted, "filter_tools_for_query should schedule background shadow evaluation"
        state = manager._states.get(("tool", entity_id))
        assert state is not None
        assert state.stable_tier == Tier.COLD
        assert state.wake_lease_until_cycle > 0
    finally:
        manager.close()


@pytest.mark.integration
def test_resolve_tiered_skill_matches_wakes_dormant_skill_via_shadow(
    fixture_pack: TierBehaviorFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = fixture_pack
    seed_skill_tiers_by_doc_id(pack, {"create-hook": Tier.DORMANT})
    entity_id = skill_entity_ids_by_doc_id(pack)["create-hook"]
    config = live_tier_config(pack, kind="skill")
    entries = build_registry_from_pack(pack)
    manager = TierManager(pack.workspace, str(pack.db_path))
    submitted = _sync_shadow_submit(monkeypatch)

    searched: list[Any] = []

    def capture_search(
        query: str,
        pool: list[Any],
        *,
        config: dict[str, Any],
        max_tokens: int | None = None,
        pruner_settings: object = None,
        skip_frontmatter_gate: bool = False,
    ) -> list[Any]:
        del query, config, max_tokens, pruner_settings, skip_frontmatter_gate
        searched.append(list(pool))
        return []

    try:
        manager.begin_request_cycle(config)
        with patch("cyt.skills.search.search_skills", side_effect=capture_search):
            with patch("cyt.tiers.manager.get_tier_manager", return_value=manager):
                resolve_tiered_skill_matches(
                    "follow the create-hook skill instructions",
                    entries,
                    config=config,
                    skip_frontmatter_gate=True,
                )
        manager.flush_pending()
        assert submitted, "resolve_tiered_skill_matches should schedule skill shadow evaluation"
        state = manager._states.get(("skill", entity_id))
        assert state is not None
        assert state.stable_tier == Tier.COLD
        assert state.wake_lease_until_cycle > 0
    finally:
        manager.close()


def _mcp_server_tool_dicts() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for fixture in load_mcp_server_tools():
        tools.append(
            stamp_tool_catalog_source(
                {
                    "name": fixture.wire_name,
                    "cyt_catalog_source": "cyt_mcp",
                    "mcp_server": fixture.mcp_server,
                    "tool_name": fixture.tool_name,
                    "description": fixture.description,
                    "inputSchema": {"type": "object"},
                },
            ),
        )
    return tools


@pytest.mark.integration
def test_filter_tools_for_query_wakes_mcp_server_description_when_all_tools_dormant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_transitions_pack(tmp_path)
    tools = _mcp_server_tool_dicts()
    entity_ids = [fixture.entity_id for fixture in load_mcp_server_tools()]
    for entity_id in entity_ids:
        seed_entity_tier(
            workspace=pack.workspace,
            db_path=pack.db_path,
            kind="tool",
            entity_id=entity_id,
            tier=Tier.DORMANT,
        )
    config = tier_transitions_config(pack, kind="tool")
    manager = TierManager(pack.workspace, str(pack.db_path))
    submitted = _sync_shadow_submit(monkeypatch)
    prompt = load_fast_wake_prompts()["mcp_server_batch"]

    try:
        manager.begin_request_cycle(config)
        with patch("cyt.tiers.manager.get_tier_manager_for_config", return_value=manager):
            filter_tools_for_query(
                tools,
                prompt,
                ["bm25"],
                config=config,
                for_hook=True,
            )
        manager.flush_pending()
        assert submitted, "filter_tools_for_query should schedule background shadow evaluation"
        server_state = manager._states.get(
            (EntityKind.MCP_SERVER, mcp_server_entity_id("gitnexus")),
        )
        assert server_state is not None
        assert server_state.stable_tier == Tier.COLD
        assert server_state.wake_lease_until_cycle > 0
        for entity_id in entity_ids:
            tool_state = manager._states.get(("tool", entity_id))
            assert tool_state is not None
            assert tool_state.stable_tier == Tier.DORMANT
            assert tool_state.effective_tier == Tier.DORMANT
    finally:
        manager.close()

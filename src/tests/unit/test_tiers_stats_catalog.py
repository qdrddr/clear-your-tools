"""Unit tests for tool/skill tier stats using shared fixture files."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.tiers.config import tier_section_config
from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.models import EntityKind, EntityTierState, Tier, TierProject
from cyt.tiers.status_detail import (
    build_kind_detail,
    enrich_tool_detail_with_catalog_discoveries,
)
from cyt.tiers.status_statistics import build_tier_statistics
from cyt.tiers.store import TierStore
from tests.support.tiers_stats_fixtures import (
    TiersStatsFixturePack,
    load_scenario,
    load_tools_catalog,
    materialize_fixture_pack,
    tier_stats_config,
)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def fixture_pack(tmp_path: Path) -> TiersStatsFixturePack:
    return materialize_fixture_pack(tmp_path)


def test_fixture_tools_catalog_loads_expected_names() -> None:
    tools = load_tools_catalog()
    names = {tool["name"] for tool in tools}
    expected = set(load_scenario().expected["discovered_tool_names"])
    assert names == expected


def test_enrich_tool_detail_discovers_untracked_catalog_tools(
    fixture_pack: TiersStatsFixturePack,
) -> None:
    cfg = tier_section_config(tier_stats_config(fixture_pack), kind="tool")
    config = tier_stats_config(fixture_pack)
    states = {
        (EntityKind.TOOL, "cyt_mcp:gitnexus_query"): EntityTierState(
            entity_id="cyt_mcp:gitnexus_query",
            kind=EntityKind.TOOL,
            stable_tier=Tier.HOT,
            effective_tier=Tier.HOT,
        ),
    }
    tracked_ids = frozenset(f"cyt_mcp:{tool['name']}" for tool in fixture_pack.tools)

    detail = build_kind_detail(
        states,
        kind=EntityKind.TOOL,
        cfg=cfg,
        wake_cycle_id=1,
        config=config,
        tracked_catalog_entity_ids=tracked_ids,
    )
    enriched = enrich_tool_detail_with_catalog_discoveries(
        detail,
        states=states,
        cfg=cfg,
        config=config,
        workspace_root=fixture_pack.workspace,
        catalog_tools=fixture_pack.tools,
        wake_cycle_id=1,
    )

    expected = load_scenario().expected
    assert enriched["histogram"] == expected["tools_by_tier"]
    entity_ids = {row["entity_id"] for items in enriched["by_tier"].values() for row in items}
    assert entity_ids == set(tracked_ids)


def test_build_tier_statistics_counts_tool_tokens(
    fixture_pack: TiersStatsFixturePack,
) -> None:
    cfg = tier_section_config(tier_stats_config(fixture_pack), kind="tool")
    config = tier_stats_config(fixture_pack)
    states: dict[tuple[str, str], EntityTierState] = {}
    tracked_ids = frozenset(f"cyt_mcp:{tool['name']}" for tool in fixture_pack.tools)

    detail = enrich_tool_detail_with_catalog_discoveries(
        build_kind_detail(
            states,
            kind=EntityKind.TOOL,
            cfg=cfg,
            wake_cycle_id=1,
            config=config,
            tracked_catalog_entity_ids=tracked_ids,
            catalog_tools=fixture_pack.tools,
        ),
        states=states,
        cfg=cfg,
        config=config,
        workspace_root=fixture_pack.workspace,
        catalog_tools=fixture_pack.tools,
        wake_cycle_id=1,
    )

    stats = build_tier_statistics(
        {"tools": detail, "skills": {"histogram": {}, "by_tier": {}}},
        catalog_tools=fixture_pack.tools,
    )
    tools_stats = stats["tools"]
    assert tools_stats["totals"]["count"] == load_scenario().expected["tools_total"]
    assert tools_stats["totals"]["tokens_known"] == load_scenario().expected["tools_total"]
    assert (
        tools_stats["totals"]["effective_tokens_known"] == load_scenario().expected["tools_total"]
    )


def test_tier_manager_status_merges_catalog_tools_and_workspace_skills(
    fixture_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
) -> None:
    config = tier_stats_config(fixture_pack)
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda _cfg, blocking=False: list(fixture_pack.tools),
    )

    manager = TierManager(fixture_pack.workspace, str(fixture_pack.db_path))
    try:
        status = manager.status(config, agent="cursor")
    finally:
        manager.close()

    expected = load_scenario().expected
    tools = status["tools"]
    assert tools["histogram"] == expected["tools_by_tier"]
    assert sum(tools["histogram"].values()) == expected["tools_total"]
    assert tools["tracked_catalog_tool_count"] == expected["tools_total"]

    hot = tools["by_tier"]["T3"]
    assert len(hot) == 1
    assert hot[0]["entity_id"] == expected["hot_tool_entity_id"]

    dormant_names = {row.get("entity_id", "").rsplit(":", 1)[-1] for row in tools["by_tier"]["T0"]}
    assert dormant_names == {"context-mode_ctx_execute", "context-mode_ctx_search"}

    skills = status["skills"]
    skill_names = {row.get("name") for row in skills["by_tier"]["T0"]}
    assert len(skill_names) >= expected["skills_min_total"]
    for name in expected["skill_names"]:
        assert name in skill_names


def test_seed_tier_db_restores_hot_tool_from_scenario(
    fixture_pack: TiersStatsFixturePack,
) -> None:
    store = TierStore.open(str(fixture_pack.db_path))
    try:
        project_id = store.get_or_create_project(str(fixture_pack.workspace))
        project = TierProject(project_id=project_id, root_path=fixture_pack.workspace)
        states = store.load_entity_states(project)
    finally:
        store.close()

    hot_key = (EntityKind.TOOL, load_scenario().expected["hot_tool_entity_id"])
    assert hot_key in states
    assert states[hot_key].effective_tier == Tier.HOT
    assert states[hot_key].stats.injected == 4.0


def test_scenario_json_is_self_consistent() -> None:
    scenario = load_scenario()
    tools = load_tools_catalog()
    assert len(tools) == scenario.expected["tools_total"]
    assert sum(scenario.expected["tools_by_tier"].values()) == scenario.expected["tools_total"]
    assert len(scenario.expected["mcp_server_names"]) == 2

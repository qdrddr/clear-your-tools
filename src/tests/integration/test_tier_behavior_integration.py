"""Integration tests: live tier behavior through manager and pruning pipeline."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
from cyt.pruners.policies import PolicyContext
from cyt.pruners.tools_filter import _tier_prune_context, filter_tools_for_query
from cyt.skills.proxy_inject import resolve_skills_for_query
from cyt.tiers.adapters.tools import tool_entity_id
from cyt.tiers.manager import NoOpTierManager, TierManager, _managers
from cyt.tiers.models import ToolsTierApplyResult
from cyt.tools.inject import format_tool_item
from cyt.tools.master_catalog import clear_master_catalog_cache
from tests.support.tier_behavior_fixtures import (
    IntegrationScenario,
    TierBehaviorFixturePack,
    build_registry_from_pack,
    live_tier_config,
    load_integration_scenarios,
    materialize_fixture_pack,
    patch_cyt_mcp_paths,
    seed_skill_tiers_by_doc_id,
    seed_tool_tiers,
    skill_fixture_key,
    write_cyt_mcp_disk_catalog_for_pack,
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


def _manager_for_pack(pack: TierBehaviorFixturePack) -> TierManager:
    return TierManager(pack.workspace, str(pack.db_path))


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    load_integration_scenarios(),
    ids=[scenario.id for scenario in load_integration_scenarios()],
)
def test_live_tool_tiers_through_manager(
    disk_catalog_pack: TierBehaviorFixturePack,
    scenario: IntegrationScenario,
) -> None:
    if not scenario.tool_tiers:
        pytest.skip("scenario has no tool tiers")

    pack = disk_catalog_pack
    seed_tool_tiers(pack, scenario.tool_tiers)
    config = live_tier_config(pack, kind="tool")
    manager = _manager_for_pack(pack)
    try:
        result = manager.apply_tools(pack.tools, config)
    finally:
        manager.close()

    eligible_names = {str(tool.get("name")) for tool in result.eligible_tools}
    assert eligible_names == scenario.expected_eligible_tool_names
    assert set(result.excluded_t0) == scenario.expected_excluded_entity_ids


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    load_integration_scenarios(),
    ids=[f"{scenario.id}-skills-partition" for scenario in load_integration_scenarios()],
)
def test_live_skill_tiers_through_manager_partition(
    fixture_pack: TierBehaviorFixturePack,
    scenario: IntegrationScenario,
) -> None:
    if not scenario.skill_tiers:
        pytest.skip("scenario has no skill tiers")

    pack = fixture_pack
    seed_skill_tiers_by_doc_id(pack, scenario.skill_tiers)
    config = live_tier_config(pack, kind="skill")
    entries = build_registry_from_pack(pack)
    manager = _manager_for_pack(pack)
    try:
        partition = manager.partition_skills(entries, config)
    finally:
        manager.close()

    search_ids = {skill_fixture_key(entry) for entry in partition.search_entries}
    t4_ids = {skill_fixture_key(entry) for entry in partition.t4_direct}
    assert search_ids == scenario.expected_search_doc_ids
    assert t4_ids == scenario.expected_t4_doc_ids


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [item for item in load_integration_scenarios() if item.id == "live_tools_one_hot_one_dormant"],
    ids=["live_tools_one_hot_one_dormant-prune"],
)
def test_filter_tools_for_query_applies_live_tool_tiers(
    disk_catalog_pack: TierBehaviorFixturePack,
    scenario: IntegrationScenario,
) -> None:
    pack = disk_catalog_pack
    seed_tool_tiers(pack, scenario.tool_tiers)
    config = live_tier_config(pack, kind="tool")
    manager = _manager_for_pack(pack)

    tools_for_prune_batches: list[list[dict[str, Any]]] = []
    real_tier_prune = _tier_prune_context

    def capture_tier_prune(
        original_tools: list[dict[str, Any]],
        config: dict[str, Any],
        *,
        ctx: PolicyContext | None,
        configured_pipeline: list[str],
        for_hook: bool,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        PolicyContext,
        PolicyContext,
        ToolsTierApplyResult,
        TierManager | NoOpTierManager,
    ]:
        result = real_tier_prune(
            original_tools,
            config,
            ctx=ctx,
            configured_pipeline=configured_pipeline,
            for_hook=for_hook,
        )
        tools_for_prune_batches.append(list(result[0]))
        return result

    try:
        with patch("cyt.pruners.tools_filter.get_tier_manager", return_value=manager):
            with patch(
                "cyt.pruners.tools_filter._tier_prune_context",
                side_effect=capture_tier_prune,
            ):
                filter_tools_for_query(
                    pack.tools,
                    scenario.query,
                    ["bm25"],
                    config=config,
                    for_hook=True,
                )
    finally:
        manager.close()

    assert tools_for_prune_batches
    sent_names = {str(tool.get("name")) for tool in tools_for_prune_batches[0]}
    assert sent_names == scenario.expected_eligible_tool_names


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [
        item
        for item in load_integration_scenarios()
        if item.id == "live_skills_t4_direct_bypasses_search"
    ],
    ids=["live_skills_t4_direct_bypasses_search-resolve"],
)
def test_resolve_skills_for_query_applies_live_skill_tiers(
    fixture_pack: TierBehaviorFixturePack,
    scenario: IntegrationScenario,
) -> None:
    pack = fixture_pack
    seed_skill_tiers_by_doc_id(pack, scenario.skill_tiers)
    config = live_tier_config(pack, kind="skill")
    entries = build_registry_from_pack(pack)

    searched_doc_ids: list[set[str]] = []

    def capture_search(
        query: str,
        entries: list[Any],
        *,
        config: dict[str, Any],
        max_tokens: int | None = None,
        pruner_settings: object = None,
        skip_frontmatter_gate: bool = False,
    ) -> list[Any]:
        del query, config, max_tokens, pruner_settings, skip_frontmatter_gate
        searched_doc_ids.append({skill_fixture_key(entry) for entry in entries})
        return []

    with patch("cyt.skills.proxy_inject.search_skills", side_effect=capture_search):
        matches = resolve_skills_for_query(
            scenario.query,
            config,
            entries=entries,
            skip_frontmatter_gate=False,
        )

    assert searched_doc_ids == [set(scenario.expected_search_doc_ids)]
    assert {skill_fixture_key(match) for match in matches} == scenario.expected_t4_doc_ids


@pytest.mark.integration
def test_combined_live_tiers_tools_and_skills(
    disk_catalog_pack: TierBehaviorFixturePack,
) -> None:
    scenario = next(
        item for item in load_integration_scenarios() if item.id == "live_tools_and_skills_combined"
    )
    pack = disk_catalog_pack
    seed_tool_tiers(pack, scenario.tool_tiers)
    seed_skill_tiers_by_doc_id(pack, scenario.skill_tiers)
    config = live_tier_config(pack, kind="both")
    manager = _manager_for_pack(pack)
    try:
        tool_result = manager.apply_tools(pack.tools, config)
        skill_partition = manager.partition_skills(build_registry_from_pack(pack), config)
    finally:
        manager.close()

    eligible_names = {str(tool.get("name")) for tool in tool_result.eligible_tools}
    assert eligible_names == scenario.expected_eligible_tool_names
    assert set(tool_result.excluded_t0) == scenario.expected_excluded_entity_ids

    t4_tool_names = {str(tool.get("name")) for tool in tool_result.t4_direct}
    assert "gitnexus_query" in t4_tool_names
    assert tool_entity_id(tool_result.t4_direct[0]) == "cyt_mcp:gitnexus_query"

    search_ids = {skill_fixture_key(entry) for entry in skill_partition.search_entries}
    t4_ids = {skill_fixture_key(entry) for entry in skill_partition.t4_direct}
    assert search_ids == scenario.expected_search_doc_ids
    assert t4_ids == scenario.expected_t4_doc_ids


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [
        item
        for item in load_integration_scenarios()
        if item.id == "live_tools_all_cold_bm25_subsets"
    ],
    ids=["live_tools_all_cold_bm25_subsets"],
)
def test_all_cold_tools_bm25_subsets_without_forcing_injection(
    disk_catalog_pack: TierBehaviorFixturePack,
    scenario: IntegrationScenario,
) -> None:
    pack = disk_catalog_pack
    seed_tool_tiers(pack, scenario.tool_tiers)
    config = live_tier_config(pack, kind="tool")
    manager = _manager_for_pack(pack)

    pipeline_batches: list[list[dict[str, Any]]] = []
    real_tier_prune = _tier_prune_context

    def capture_pipeline(
        original_tools: list[dict[str, Any]],
        config: dict[str, Any],
        *,
        ctx: PolicyContext | None,
        configured_pipeline: list[str],
        for_hook: bool,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        PolicyContext,
        PolicyContext,
        ToolsTierApplyResult,
        TierManager | NoOpTierManager,
    ]:
        result = real_tier_prune(
            original_tools,
            config,
            ctx=ctx,
            configured_pipeline=configured_pipeline,
            for_hook=for_hook,
        )
        pipeline_batches.append(list(result[0]))
        return result

    try:
        with patch("cyt.pruners.tools_filter.get_tier_manager", return_value=manager):
            with patch(
                "cyt.pruners.tools_filter._tier_prune_context",
                side_effect=capture_pipeline,
            ):
                result = filter_tools_for_query(
                    pack.tools,
                    scenario.query,
                    ["bm25"],
                    config=config,
                    for_hook=True,
                )
    finally:
        manager.close()

    assert pipeline_batches
    pipeline_tools = pipeline_batches[0]
    assert {str(tool.get("name")) for tool in pipeline_tools} == scenario.expected_eligible_tool_names
    for tool in pipeline_tools:
        assert not (tool.get("input_schema") or tool.get("inputSchema"))

    pruned_names = {str(tool.get("name")) for tool in result.tools or []}
    assert scenario.expected_pruned_tool_names <= pruned_names
    assert scenario.expected_pruned_must_exclude.isdisjoint(pruned_names)
    assert len(pruned_names) < len(pipeline_tools)

    for tool_name in scenario.expected_injection_omits_schema_for:
        tool = next(item for item in result.tools or [] if item.get("name") == tool_name)
        formatted = format_tool_item(tool)
        assert "input_schema" not in formatted


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [
        item
        for item in load_integration_scenarios()
        if item.id == "live_skills_active_search_pool_headers_only"
    ],
    ids=["live_skills_active_search_pool_headers_only"],
)
def test_active_skills_search_pool_is_headers_only_before_bm25(
    fixture_pack: TierBehaviorFixturePack,
    scenario: IntegrationScenario,
) -> None:
    from dataclasses import replace
    from pathlib import Path

    from cyt.tiers.adapters.skills import prepare_skill_entries_for_tier_search

    pack = fixture_pack
    seed_skill_tiers_by_doc_id(pack, scenario.skill_tiers)
    config = live_tier_config(pack, kind="skill")
    entries = build_registry_from_pack(pack)
    manager = _manager_for_pack(pack)
    try:
        partition = manager.partition_skills(entries, config)
    finally:
        manager.close()

    hydrated: list[Any] = []
    for entry in partition.search_entries:
        markdown = str(entry.document.get("markdown") or entry.document.get("content") or "")
        if not markdown.strip():
            source = Path(entry.source_path)
            if source.is_file():
                markdown = source.read_text(encoding="utf-8")
        document = dict(entry.document)
        document["markdown"] = markdown
        document["content"] = markdown
        hydrated.append(replace(entry, document=document))

    search_pool = prepare_skill_entries_for_tier_search(
        hydrated,
        partition.tier_by_skill,
    )
    searched_ids = {skill_fixture_key(entry) for entry in search_pool}
    assert searched_ids == scenario.expected_search_doc_ids

    for entry in search_pool:
        doc_id = skill_fixture_key(entry)
        markdown = str(entry.document.get("markdown") or entry.document.get("content") or "")
        for fragment in scenario.expected_skill_search_excludes_body.get(doc_id, ()):
            assert fragment not in markdown

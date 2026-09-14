"""Integration tests: tier stamping, legends, and pre-exposure through live pipelines."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
from cyt.injection.pre_exposure_context import PreExposureContext
from cyt.injection.tier_legend import TOOL_TIER_LEGEND
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.skills.cli import _search_skills_for_user_prompt
from cyt.skills.inject import format_skill_item
from cyt.skills.proxy_inject import resolve_skills_for_query
from cyt.tiers.adapters.skills import resolve_tiered_skill_matches
from cyt.tiers.manager import TierManager, _managers
from cyt.tools.hook import gate_and_format_hook_tools
from cyt.tools.inject import format_tool_item
from cyt.tools.master_catalog import clear_master_catalog_cache
from tests.support.tier_behavior_fixtures import (
    TierBehaviorFixturePack,
    build_registry_from_pack,
    live_tier_config,
    materialize_fixture_pack,
    patch_cyt_mcp_paths,
    seed_skill_tiers_by_doc_id,
    seed_tool_tiers,
    skill_fixture_key,
    write_cyt_mcp_disk_catalog_for_pack,
)
from tests.support.tier_injection_fixtures import (
    behavior_scenario_by_id,
    load_injection_integration_scenarios,
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


@pytest.mark.parametrize(
    "scenario",
    [
        item
        for item in load_injection_integration_scenarios()
        if item.expected_stamped_tools
    ],
    ids=[item.id for item in load_injection_integration_scenarios() if item.expected_stamped_tools],
)
def test_filter_tools_for_query_stamps_cyt_injection_tier(
    disk_catalog_pack: TierBehaviorFixturePack,
    scenario,
) -> None:
    behavior = behavior_scenario_by_id(scenario.behavior_scenario_id)
    pack = disk_catalog_pack
    seed_tool_tiers(pack, behavior.tool_tiers)
    config = live_tier_config(pack, kind="tool")
    manager = _manager_for_pack(pack)
    query = scenario.query or behavior.query
    try:
        with patch("cyt.pruners.tools_filter.get_tier_manager", return_value=manager):
            result = filter_tools_for_query(
                pack.tools,
                query,
                ["bm25"],
                config=config,
                for_hook=True,
            )
    finally:
        manager.close()

    assert result.tools
    stamped = {
        str(tool.get("name") or ""): str(tool.get("cyt_injection_tier") or "")
        for tool in result.tools
    }
    for tool_name, expected_tier in scenario.expected_stamped_tools.items():
        assert stamped.get(tool_name) == expected_tier


@pytest.mark.parametrize(
    "scenario",
    [
        item
        for item in load_injection_integration_scenarios()
        if item.expected_skill_tiers
    ],
    ids=[item.id for item in load_injection_integration_scenarios() if item.expected_skill_tiers],
)
def test_resolve_tiered_skill_matches_sets_injection_tier(
    fixture_pack: TierBehaviorFixturePack,
    scenario,
) -> None:
    behavior = behavior_scenario_by_id(scenario.behavior_scenario_id)
    pack = fixture_pack
    seed_skill_tiers_by_doc_id(pack, behavior.skill_tiers)
    config = live_tier_config(pack, kind="skill")
    entries = build_registry_from_pack(pack)

    with patch("cyt.skills.search.search_skills", return_value=[]):
        matches = resolve_tiered_skill_matches(
            behavior.query,
            entries,
            config=config,
            skip_frontmatter_gate=True,
        )

    tiers = {skill_fixture_key(match): match.injection_tier for match in matches}
    for doc_id, expected_tier in scenario.expected_skill_tiers.items():
        assert tiers.get(doc_id) == expected_tier


def test_hook_search_skills_uses_tiered_resolver(
    fixture_pack: TierBehaviorFixturePack,
) -> None:
    behavior = behavior_scenario_by_id("live_skills_t4_direct_bypasses_search")
    pack = fixture_pack
    seed_skill_tiers_by_doc_id(pack, behavior.skill_tiers)
    config = live_tier_config(pack, kind="skill")

    with (
        patch("cyt.skills.cli.build_registry_for_hook_payload", return_value=build_registry_from_pack(pack)),
        patch("cyt.skills.search.search_skills", return_value=[]),
    ):
        matches, _, _ = _search_skills_for_user_prompt(
            behavior.query,
            config,
            max_tokens=4000,
            plain_output=False,
            debug=False,
            payload={"cyt_skills": []},
        )

    assert {skill_fixture_key(match) for match in matches} == behavior.expected_t4_doc_ids
    assert all(match.injection_tier == "t4" for match in matches)


@pytest.mark.parametrize(
    "scenario",
    [
        item
        for item in load_injection_integration_scenarios()
        if item.expects_tool_legend or item.expects_tier_attr_on_tools
    ],
    ids=[
        item.id
        for item in load_injection_integration_scenarios()
        if item.expects_tool_legend or item.expects_tier_attr_on_tools
    ],
)
def test_gate_and_format_hook_tools_emits_legend_and_tier_attrs(
    disk_catalog_pack: TierBehaviorFixturePack,
    scenario,
) -> None:
    behavior = behavior_scenario_by_id(scenario.behavior_scenario_id)
    pack = disk_catalog_pack
    seed_tool_tiers(pack, behavior.tool_tiers)
    config = live_tier_config(pack, kind="tool")
    manager = _manager_for_pack(pack)
    query = scenario.query or behavior.query
    try:
        with patch("cyt.pruners.tools_filter.get_tier_manager", return_value=manager):
            result = filter_tools_for_query(
                pack.tools,
                query,
                ["bm25"],
                config=config,
                for_hook=True,
            )
    finally:
        manager.close()

    assert result.tools
    cyt_tools = [
        tool
        for tool in result.tools
        if str(tool.get("cyt_catalog_source") or "") == "cyt_mcp"
    ]
    formatted, _logs = gate_and_format_hook_tools(
        cyt_tools,
        config=config,
        payload={},
        session_text="",
        catalog_tools=pack.tools,
        prune_results={"cyt_mcp": result},
    )

    if scenario.expects_tool_legend:
        assert TOOL_TIER_LEGEND in formatted
    for tool_name in scenario.expects_tier_attr_on_tools:
        assert f"name='{tool_name}'" in formatted
        tool = next(item for item in cyt_tools if item.get("name") == tool_name)
        tier = str(tool.get("cyt_injection_tier") or "")
        assert f"tier='{tier}'" in formatted


def test_second_turn_pre_exposure_skips_t4_tool_and_skill(
    fixture_pack: TierBehaviorFixturePack,
) -> None:
    from cyt.injection.pre_exposure_pipeline import gate_and_filter_skills
    from cyt.skills.search import MatchedSkill

    skill_markdown = (
        "---\nname: create-hook\ndescription: Agent hooks for sessions.\n---\n\n"
        "# Create Hook\n\nSubmit prompts\n"
    )
    t4_match = MatchedSkill(
        doc_id="create-hook",
        file_path=str(fixture_pack.skill_paths_by_doc_id["create-hook"]),
        markdown=skill_markdown,
        name="create-hook",
        score=1.0,
        token_count=10,
        injection_tier="t4",
    )
    prior_skill = format_skill_item(t4_match, full=True)

    t4_tool = {
        "name": "gitnexus_query",
        "description": "Graph query tool",
        "cyt_catalog_source": "cyt_mcp",
        "cyt_injection_tier": "t4",
        "input_schema": {
            "type": "object",
            "properties": {"search_query": {"type": "string"}},
            "required": ["search_query"],
        },
    }
    prior_tool = format_tool_item(t4_tool)
    session_text = f"{prior_skill}\n{prior_tool}"
    payload = {
        "cyt_agent": "cursor",
        "cyt_transcript": [
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": session_text}]},
            },
        ],
    }

    ctx = PreExposureContext.for_hook_payload(payload, allow_file_read=False)
    config = live_tier_config(fixture_pack, kind="both")
    formatted, _logs = gate_and_format_hook_tools(
        [t4_tool],
        config=config,
        payload=payload,
        session_text=session_text,
        prune_results={"cyt_mcp": type("R", (), {"tools": [t4_tool]})()},
    )
    assert "gitnexus_query" not in formatted

    gated, _ = gate_and_filter_skills([t4_match], config=config, ctx=ctx)
    assert gated == []

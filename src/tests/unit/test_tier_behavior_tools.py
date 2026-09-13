"""Unit tests for live tool tier behavior using shared fixture files."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.tiers.adapters.tools import apply_tool_tiers, tool_entity_id
from cyt.tiers.models import Tier
from cyt.tiers.tool_token_materialization import (
    clear_carried_token_memo,
    effective_tool_token_count,
    input_schema_from_tool,
    schema_for_tier,
)
from tests.support.tier_behavior_fixtures import (
    SKILL_FIXTURE_NAMES,
    TierBehaviorFixturePack,
    iter_skill_tier_cases,
    iter_tool_tier_cases,
    load_integration_scenarios,
    load_skill_scenarios,
    load_tool_scenarios,
    materialize_fixture_pack,
    tool_by_entity_id,
)


@pytest.fixture
def fixture_pack(tmp_path: Path) -> TierBehaviorFixturePack:
    return materialize_fixture_pack(tmp_path)


def test_fixture_tools_catalog_covers_all_scenarios(fixture_pack: TierBehaviorFixturePack) -> None:
    catalog_ids = {tool_entity_id(tool) for tool in fixture_pack.tools}
    for scenario in load_tool_scenarios():
        assert scenario.entity_id in catalog_ids


def test_scenario_json_is_self_consistent() -> None:
    tool_scenarios = load_tool_scenarios()
    skill_scenarios = load_skill_scenarios()
    integration = load_integration_scenarios()

    assert len(tool_scenarios) == 3
    assert len(skill_scenarios) == 3
    assert len(integration) == 3
    assert len(SKILL_FIXTURE_NAMES) == 3

    for tool_scenario in tool_scenarios:
        assert len(tool_scenario.tier_expectations) == 5
    for skill_scenario in skill_scenarios:
        assert len(skill_scenario.tier_expectations) == 5

    assert len(iter_tool_tier_cases()) == 15
    assert len(iter_skill_tier_cases()) == 15

    skill_keys = {scenario.doc_id for scenario in skill_scenarios}
    assert skill_keys == {name.replace(".md", "") for name in SKILL_FIXTURE_NAMES}


@pytest.mark.parametrize(
    ("entity_id", "tool_name", "expectation"),
    [
        (entity_id, tool_name, expectation)
        for entity_id, tool_name, expectation in iter_tool_tier_cases()
    ],
    ids=[
        f"{tool_name}-{expectation.raw['tier']}"
        for _entity_id, tool_name, expectation in iter_tool_tier_cases()
    ],
)
def test_apply_tool_tiers_matches_fixture_expectations(
    fixture_pack: TierBehaviorFixturePack,
    entity_id: str,
    tool_name: str,
    expectation: object,
) -> None:
    from tests.support.tier_behavior_fixtures import ToolTierExpectation

    assert isinstance(expectation, ToolTierExpectation)
    tool = tool_by_entity_id(fixture_pack, entity_id)
    tier_map = {entity_id: expectation.tier}
    result = apply_tool_tiers([tool], tier_for_tool=tier_map, apply=True)

    raw = expectation.raw
    eligible_names = {str(item.get("name")) for item in result.eligible_tools}
    t4_names = {str(item.get("name")) for item in result.t4_direct}

    if raw.get("excluded"):
        assert entity_id in result.excluded_t0
    else:
        assert entity_id not in result.excluded_t0

    if raw.get("in_eligible"):
        assert tool_name in eligible_names
    else:
        assert tool_name not in eligible_names

    if raw.get("in_t4_direct"):
        assert tool_name in t4_names
    else:
        assert tool_name not in t4_names

    expected_policy = raw.get("policy")
    if expected_policy is None:
        assert tool_name not in result.policy_overrides
    else:
        assert result.policy_overrides.get(tool_name) == expected_policy


@pytest.mark.parametrize(
    ("entity_id", "tool_name", "expectation"),
    [
        (entity_id, tool_name, expectation)
        for entity_id, tool_name, expectation in iter_tool_tier_cases()
    ],
    ids=[
        f"{tool_name}-schema-{expectation.raw['tier']}"
        for _entity_id, tool_name, expectation in iter_tool_tier_cases()
    ],
)
def test_schema_for_tier_matches_fixture_expectations(
    fixture_pack: TierBehaviorFixturePack,
    entity_id: str,
    tool_name: str,
    expectation: object,
) -> None:
    from tests.support.tier_behavior_fixtures import ToolTierExpectation

    assert isinstance(expectation, ToolTierExpectation)
    tool = tool_by_entity_id(fixture_pack, entity_id)
    schema = input_schema_from_tool(tool)
    tier_label = f"T{expectation.tier.value}"
    trimmed = schema_for_tier(schema, tier_label)
    raw = expectation.raw

    if "schema_property_keys" in raw:
        assert list(trimmed.get("properties", {}).keys()) == raw["schema_property_keys"]

    if "schema_required_only" in raw:
        required = trimmed.get("required")
        assert isinstance(required, list)
        assert required == raw["schema_required_only"]
        assert list(trimmed.get("properties", {}).keys()) == raw["schema_required_only"]


@pytest.mark.parametrize(
    ("entity_id", "tool_name", "expectation"),
    [
        (entity_id, tool_name, expectation)
        for entity_id, tool_name, expectation in iter_tool_tier_cases()
    ],
    ids=[
        f"{tool_name}-tokens-{expectation.raw['tier']}"
        for _entity_id, tool_name, expectation in iter_tool_tier_cases()
    ],
)
def test_effective_tool_token_count_matches_fixture(
    fixture_pack: TierBehaviorFixturePack,
    entity_id: str,
    tool_name: str,
    expectation: object,
) -> None:
    from tests.support.tier_behavior_fixtures import ToolTierExpectation

    assert isinstance(expectation, ToolTierExpectation)
    clear_carried_token_memo()
    tool = tool_by_entity_id(fixture_pack, entity_id)
    tier_label = f"T{expectation.tier.value}"
    count = effective_tool_token_count(tool, tier_label)

    if "effective_tokens" in expectation.raw:
        assert count == expectation.raw["effective_tokens"]
    elif expectation.tier == Tier.DORMANT:
        assert count == 0


def test_tool_token_counts_are_monotonic_across_tiers(
    fixture_pack: TierBehaviorFixturePack,
) -> None:
    clear_carried_token_memo()
    tool = fixture_pack.tools[0]
    counts = [
        effective_tool_token_count(tool, f"T{tier.value}")
        for tier in (Tier.COLD, Tier.ACTIVE, Tier.HOT, Tier.EXTRA_HOT)
    ]
    assert counts[0] <= counts[1] <= counts[2]
    assert counts[2] == counts[3]

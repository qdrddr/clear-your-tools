"""Shadow-mode regression tests: tiers observe but do not filter."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.tiers.adapters.skills import partition_skill_entries, skill_entity_id
from cyt.tiers.adapters.tools import apply_tool_tiers, tool_entity_id
from cyt.tiers.models import Tier
from tests.support.tier_behavior_fixtures import (
    TierBehaviorFixturePack,
    build_registry_from_pack,
    materialize_fixture_pack,
    skill_fixture_key,
)


@pytest.fixture
def fixture_pack(tmp_path: Path) -> TierBehaviorFixturePack:
    return materialize_fixture_pack(tmp_path)


def test_apply_tool_tiers_shadow_mode_keeps_dormant_tools(
    fixture_pack: TierBehaviorFixturePack,
) -> None:
    tools = list(fixture_pack.tools)
    tier_map = {tool_entity_id(tool): Tier.DORMANT for tool in tools}
    result = apply_tool_tiers(tools, tier_for_tool=tier_map, apply=False)
    assert result.excluded_t0 == []
    assert len(result.eligible_tools) == len(tools)
    assert result.t4_direct == []


def test_partition_skills_shadow_mode_keeps_dormant_entries(
    fixture_pack: TierBehaviorFixturePack,
) -> None:
    entries = build_registry_from_pack(fixture_pack)
    tier_map = {skill_entity_id(entry): Tier.DORMANT for entry in entries}
    partition = partition_skill_entries(entries, tier_for_skill=tier_map, apply=False)
    assert {skill_fixture_key(entry) for entry in partition.search_entries} == {
        skill_fixture_key(entry) for entry in entries
    }
    assert partition.t4_direct == []

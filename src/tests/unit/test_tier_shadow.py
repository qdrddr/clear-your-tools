"""Unit tests for background tier shadow evaluation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.tiers.config import tier_section_config
from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.adapters.tools import mcp_server_entity_id
from cyt.tiers.models import EntityKind, EntityTierState, Tier
from cyt.tiers.shadow import (
    _lexical_shadow_hits,
    _lexical_skill_shadow_hits,
    record_shadow_hits,
)
from tests.support.tier_shadow_fixtures import (
    load_lexical_hit_scenarios,
    load_mcp_server_tools,
    load_record_shadow_scenarios,
    load_wake_cycle_id,
    mcp_server_tools_by_id,
)
from tests.support.tier_transitions_fixtures import (
    materialize_transitions_pack,
    tier_transitions_config,
)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.mark.parametrize(
    "scenario",
    load_lexical_hit_scenarios(),
    ids=[scenario.id for scenario in load_lexical_hit_scenarios()],
)
def test_lexical_shadow_hits_from_fixture(scenario) -> None:
    if scenario.tool is not None:
        hits = _lexical_shadow_hits(
            scenario.query,
            [scenario.entity_id],
            {scenario.entity_id: scenario.tool},
        )
    else:
        assert scenario.skill is not None
        hits = _lexical_skill_shadow_hits(
            scenario.query,
            [scenario.entity_id],
            {scenario.entity_id: scenario.skill},
        )
    if scenario.expected_score <= 0.0:
        assert hits == []
        return
    assert hits
    entity_id, score = hits[0]
    assert entity_id == scenario.entity_id
    assert score == pytest.approx(scenario.expected_score)


@pytest.mark.parametrize(
    "scenario",
    load_record_shadow_scenarios(),
    ids=[scenario.id for scenario in load_record_shadow_scenarios()],
)
def test_record_shadow_hits_from_fixture(scenario, tmp_path: Path) -> None:
    pack = materialize_transitions_pack(tmp_path)
    config = tier_transitions_config(pack, kind=scenario.kind)
    cfg = tier_section_config(config, kind=scenario.kind)
    wake_cycle_id = load_wake_cycle_id()

    if scenario.mcp_server_batch:
        tools_by_id = mcp_server_tools_by_id()
        states: dict[tuple[str, str], EntityTierState] = {}
        for fixture in load_mcp_server_tools():
            states[("tool", fixture.entity_id)] = EntityTierState(
                entity_id=fixture.entity_id,
                kind="tool",
                stable_tier=Tier.DORMANT,
                effective_tier=Tier.DORMANT,
            )
        primary = scenario.primary_entity_id or "cyt_mcp:gitnexus_query"
        hits = [(primary, scenario.hit_score)]
        record_shadow_hits(
            states,
            kind="tool",
            hits=hits,
            cfg=cfg,
            wake_cycle_id=wake_cycle_id,
            tools_by_id=tools_by_id,
        )
        if scenario.expect_tools_dormant:
            for fixture in load_mcp_server_tools():
                state = states[("tool", fixture.entity_id)]
                assert state.stable_tier == Tier.DORMANT
                assert state.effective_tier == Tier.DORMANT
        if scenario.expect_server_cold:
            server = scenario.mcp_server or "gitnexus"
            server_state = states[(EntityKind.MCP_SERVER, mcp_server_entity_id(server))]
            assert server_state.stable_tier == Tier.COLD
            assert server_state.effective_tier == Tier.COLD
            assert server_state.wake_lease_until_cycle > 0
        return

    assert scenario.entity_id is not None
    key = (scenario.kind, scenario.entity_id)
    states = {
        key: EntityTierState(
            entity_id=scenario.entity_id,
            kind=scenario.kind,
            stable_tier=Tier.DORMANT,
            effective_tier=Tier.DORMANT,
        ),
    }
    record_shadow_hits(
        states,
        kind=scenario.kind,
        hits=scenario.hits,
        cfg=cfg,
        wake_cycle_id=wake_cycle_id,
    )
    state = states[key]
    if scenario.expect_wake:
        assert state.stable_tier == Tier.COLD
        assert state.effective_tier == Tier.COLD
        assert state.wake_lease_until_cycle > 0
    elif scenario.expect_wake is False:
        assert state.stable_tier == Tier.DORMANT
        assert state.effective_tier == Tier.DORMANT


def test_apply_shadow_tool_hits_skips_untracked_catalog_source(
    tmp_path: Path,
) -> None:
    pack = materialize_transitions_pack(tmp_path)
    config = tier_transitions_config(pack, kind="tool")
    manager = TierManager(pack.workspace, str(pack.db_path))
    try:
        manager.begin_request_cycle(config)
        manager.apply_shadow_tool_hits(
            [("executor:unknown_tool", 0.95)],
            config,
        )
        assert ("tool", "executor:unknown_tool") not in manager._states
    finally:
        manager.close()

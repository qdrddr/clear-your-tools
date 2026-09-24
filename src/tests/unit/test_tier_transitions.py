"""Unit tests for slow-clock epoch and fast/hot tier transitions (tools and skills)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.tiers.config import tier_section_config
from cyt.tiers.evaluator import evaluate_slow_clock
from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.models import EpochState, Tier
from cyt.tiers.wake import (
    evaluate_fast_wake,
    fast_promote_on_optional_use,
    fast_promote_on_tool_use,
)
from tests.support.tier_transitions_fixtures import (
    FastHotScenario,
    SlowClockScenario,
    TempExpiryScenario,
    TierTransitionsFixturePack,
    entity_state_from_scenario,
    expire_epoch_on_manager,
    load_fast_hot_scenarios,
    load_slow_clock_scenarios,
    load_temp_expiry_scenarios,
    materialize_transitions_pack,
    tier_transitions_config,
    tool_dict_for_pack,
)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def fixture_pack(tmp_path: Path) -> TierTransitionsFixturePack:
    return materialize_transitions_pack(tmp_path)


def _slow_cfg(
    pack: TierTransitionsFixturePack,
    scenario: SlowClockScenario,
) -> object:
    config = tier_transitions_config(pack, kind=scenario.kind)
    section = dict(config.get("skills" if scenario.kind == "skill" else "tools") or {})
    tiers = dict(section.get("tiers") or {})
    tiers.update(scenario.config_overrides)
    section["tiers"] = tiers
    config["skills" if scenario.kind == "skill" else "tools"] = section
    return tier_section_config(config, kind=scenario.kind)


def _state_for_slow_scenario(scenario: SlowClockScenario) -> object:
    return entity_state_from_scenario(
        kind=scenario.kind,
        entity_id=scenario.entity_id,
        stable_tier=scenario.stable_tier,
        effective_tier=scenario.effective_tier,
        stats=scenario.stats,
    )


@pytest.mark.parametrize(
    "scenario",
    load_slow_clock_scenarios(),
    ids=[scenario.id for scenario in load_slow_clock_scenarios()],
)
def test_slow_clock_transitions_from_fixture(
    fixture_pack: TierTransitionsFixturePack,
    scenario: SlowClockScenario,
) -> None:
    state = _state_for_slow_scenario(scenario)
    key = (scenario.kind, scenario.entity_id)
    transitions = evaluate_slow_clock({key: state}, cfg=_slow_cfg(fixture_pack, scenario), epoch=EpochState())
    assert any(t.reason == scenario.expected_reason for t in transitions)
    assert state.stable_tier == scenario.expected_stable_tier


@pytest.mark.parametrize(
    "scenario",
    [s for s in load_fast_hot_scenarios() if s.kind == "tool"],
    ids=[scenario.id for scenario in load_fast_hot_scenarios() if scenario.kind == "tool"],
)
def test_fast_hot_tool_temp_jump_from_fixture(
    fixture_pack: TierTransitionsFixturePack,
    scenario: FastHotScenario,
) -> None:
    state = entity_state_from_scenario(
        kind=scenario.kind,
        entity_id=scenario.entity_id,
        stable_tier=scenario.stable_tier,
        effective_tier=scenario.effective_tier,
        stats=scenario.stats,
    )
    cfg = tier_section_config(tier_transitions_config(fixture_pack), kind="tool")
    if scenario.use_optional_promotion:
        transition = fast_promote_on_optional_use(state, cfg=cfg)
    else:
        transition = fast_promote_on_tool_use(state, cfg=cfg)
    assert transition is not None
    assert transition.reason == scenario.expected_reason
    assert state.effective_tier == scenario.expected_effective_tier
    assert state.stable_tier == scenario.expected_stable_tier
    if scenario.expect_temp_promotion:
        assert state.temp_promotion_until_ms is not None


@pytest.mark.parametrize(
    "scenario",
    [s for s in load_fast_hot_scenarios() if s.kind == "skill" and s.expected_reason == "fast_wake"],
    ids=[
        scenario.id
        for scenario in load_fast_hot_scenarios()
        if scenario.kind == "skill" and scenario.expected_reason == "fast_wake"
    ],
)
def test_fast_wake_skill_jump_from_fixture(
    fixture_pack: TierTransitionsFixturePack,
    scenario: FastHotScenario,
) -> None:
    state = entity_state_from_scenario(
        kind=scenario.kind,
        entity_id=scenario.entity_id,
        stable_tier=scenario.stable_tier,
        effective_tier=scenario.effective_tier,
        stats=scenario.stats,
        sleep_cooldown_until_cycle=scenario.sleep_cooldown_until_cycle,
    )
    cfg = tier_section_config(tier_transitions_config(fixture_pack), kind="skill")
    transition = evaluate_fast_wake(state, cfg=cfg, wake_cycle_id=scenario.wake_cycle_id)
    assert transition is not None
    assert transition.reason == scenario.expected_reason
    assert state.effective_tier == scenario.expected_effective_tier
    assert state.stable_tier == scenario.expected_stable_tier
    assert state.temp_promotion_until_ms is None


@pytest.mark.parametrize(
    "scenario",
    [s for s in load_fast_hot_scenarios() if s.kind == "skill" and s.expected_reason == "skill_used"],
    ids=[
        scenario.id
        for scenario in load_fast_hot_scenarios()
        if scenario.kind == "skill" and scenario.expected_reason == "skill_used"
    ],
)
def test_fast_hot_skill_temp_jump_from_fixture(
    fixture_pack: TierTransitionsFixturePack,
    scenario: FastHotScenario,
) -> None:
    state = entity_state_from_scenario(
        kind=scenario.kind,
        entity_id=scenario.entity_id,
        stable_tier=scenario.stable_tier,
        effective_tier=scenario.effective_tier,
        stats=scenario.stats,
    )
    cfg = tier_section_config(tier_transitions_config(fixture_pack), kind="skill")
    transition = fast_promote_on_tool_use(state, cfg=cfg)
    assert transition is not None
    assert transition.reason == scenario.expected_reason
    assert state.effective_tier == scenario.expected_effective_tier
    assert state.stable_tier == scenario.expected_stable_tier
    if scenario.expect_temp_promotion:
        assert state.temp_promotion_until_ms is not None


def test_manager_slow_epoch_demotes_unused_tool(
    fixture_pack: TierTransitionsFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = next(s for s in load_slow_clock_scenarios() if s.id == "tool_demote_t2_unused")
    state = _state_for_slow_scenario(scenario)
    seed_epoch = EpochState(epoch_id=3, epoch_start_ms=fixture_pack.fixed_now_ms - 400_000)
    from tests.support.tier_transitions_fixtures import seed_entity_state

    seed_entity_state(fixture_pack, state, epoch=seed_epoch)

    config = tier_transitions_config(fixture_pack)
    monkeypatch.setattr("time.time", lambda: fixture_pack.epoch_expired_now_ms / 1000)
    manager = TierManager(fixture_pack.workspace, str(fixture_pack.db_path))
    try:
        expire_epoch_on_manager(manager, now_ms=fixture_pack.epoch_expired_now_ms)
        manager.record_tool_candidates([], config=config)
        persisted = manager._states.get(("tool", scenario.entity_id))
        assert persisted is not None
        assert persisted.stable_tier == Tier.COLD
        assert manager._epoch.epoch_id == seed_epoch.epoch_id + 1
    finally:
        manager.close()


def test_manager_fast_hot_then_epoch_crystallizes_tool(
    fixture_pack: TierTransitionsFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support.tier_transitions_fixtures import seed_entity_state

    entity_id = fixture_pack.tool.entity_id
    seed_entity_state(
        fixture_pack,
        entity_state_from_scenario(
            kind="tool",
            entity_id=entity_id,
            stable_tier=Tier.COLD,
        ),
        epoch=EpochState(epoch_id=2, epoch_start_ms=fixture_pack.fixed_now_ms - 60_000),
    )
    config = tier_transitions_config(fixture_pack)
    tool = tool_dict_for_pack(fixture_pack)
    monkeypatch.setattr("time.time", lambda: fixture_pack.fixed_now_ms / 1000)

    manager = TierManager(fixture_pack.workspace, str(fixture_pack.db_path))
    try:
        manager.record_tool_attempt(tool, config=config, success=True)
        hot_state = manager._states.get(("tool", entity_id))
        assert hot_state is not None
        assert hot_state.effective_tier == Tier.HOT
        assert hot_state.stable_tier == Tier.COLD
        assert hot_state.temp_promotion_until_ms is not None

        expire_epoch_on_manager(manager, now_ms=fixture_pack.epoch_expired_now_ms)
        monkeypatch.setattr("time.time", lambda: fixture_pack.epoch_expired_now_ms / 1000)
        manager.record_tool_candidates([], config=config)
        crystallized = manager._states.get(("tool", entity_id))
        assert crystallized is not None
        assert crystallized.stable_tier == Tier.HOT
        assert crystallized.effective_tier == Tier.HOT
        assert crystallized.temp_promotion_until_ms is None
    finally:
        manager.close()


@pytest.mark.parametrize(
    "scenario",
    load_temp_expiry_scenarios(),
    ids=[scenario.id for scenario in load_temp_expiry_scenarios()],
)
def test_temp_expiry_transitions_from_fixture(
    fixture_pack: TierTransitionsFixturePack,
    scenario: TempExpiryScenario,
) -> None:
    from cyt.tiers.evaluator import expire_temporary_promotions
    from cyt.tiers.models import EntityTierState, EffectiveStats

    stats = EffectiveStats()
    for key, value in scenario.stats.items():
        if hasattr(stats, key):
            setattr(stats, key, float(value))
    state = EntityTierState(
        entity_id=scenario.entity_id,
        kind=scenario.kind,
        stable_tier=scenario.stable_tier,
        effective_tier=scenario.effective_tier,
        overlap_tier=scenario.overlap_tier,
        temp_promotion_until_ms=scenario.temp_promotion_until_ms,
        stats=stats,
    )
    transitions = expire_temporary_promotions(
        {(scenario.kind, scenario.entity_id): state},
        now_ms=scenario.now_ms,
    )
    assert transitions
    assert transitions[0].reason == scenario.expected_reason
    assert state.stable_tier == scenario.expected_stable_tier


def test_manager_records_optional_property_use_as_fast_hot(
    fixture_pack: TierTransitionsFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support.tier_transitions_fixtures import seed_entity_state

    entity_id = fixture_pack.tool.entity_id
    seed_entity_state(
        fixture_pack,
        entity_state_from_scenario(kind="tool", entity_id=entity_id, stable_tier=Tier.COLD),
        epoch=EpochState(epoch_id=2, epoch_start_ms=fixture_pack.fixed_now_ms - 60_000),
    )
    config = tier_transitions_config(fixture_pack)
    tool = tool_dict_for_pack(fixture_pack)
    monkeypatch.setattr("time.time", lambda: fixture_pack.fixed_now_ms / 1000)

    manager = TierManager(fixture_pack.workspace, str(fixture_pack.db_path))
    try:
        manager.record_tool_attempt(tool, config=config, success=True, optional_used=True)
        state = manager._states.get(("tool", entity_id))
        assert state is not None
        assert state.effective_tier == Tier.HOT
        assert state.stable_tier == Tier.COLD
        assert state.temp_promotion_until_ms is not None
    finally:
        manager.close()


def test_manager_slow_epoch_promotes_skill(
    fixture_pack: TierTransitionsFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = next(s for s in load_slow_clock_scenarios() if s.id == "skill_promote_t2_t3")
    state = _state_for_slow_scenario(scenario)
    from tests.support.tier_transitions_fixtures import seed_entity_state

    seed_entity_state(
        fixture_pack,
        state,
        epoch=EpochState(epoch_id=8, epoch_start_ms=fixture_pack.fixed_now_ms - 400_000),
    )
    config = tier_transitions_config(fixture_pack, kind="skill")
    monkeypatch.setattr("time.time", lambda: fixture_pack.epoch_expired_now_ms / 1000)

    manager = TierManager(fixture_pack.workspace, str(fixture_pack.db_path))
    try:
        expire_epoch_on_manager(manager, now_ms=fixture_pack.epoch_expired_now_ms)
        manager.record_skill_candidates([], config=config)
        promoted = manager._states.get(("skill", scenario.entity_id))
        assert promoted is not None
        assert promoted.stable_tier == Tier.HOT
    finally:
        manager.close()

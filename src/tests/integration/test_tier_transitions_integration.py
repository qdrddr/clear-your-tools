"""Integration tests for tier manager slow epoch and fast/hot transitions."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.models import EpochState
from tests.support.tier_transitions_fixtures import (
    ManagerIntegrationScenario,
    TierTransitionsFixturePack,
    entity_state_from_scenario,
    expire_epoch_on_manager,
    latest_epoch_log_reasons,
    load_manager_integration_scenarios,
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


def _seed_scenario(
    pack: TierTransitionsFixturePack,
    scenario: ManagerIntegrationScenario,
) -> None:
    from tests.support.tier_transitions_fixtures import seed_entity_state

    state = entity_state_from_scenario(
        kind=scenario.kind,
        entity_id=scenario.entity_id,
        stable_tier=scenario.seed_tier,
        stats=scenario.stats,
    )
    epoch_age_ms = 60_000 if scenario.path.startswith("fast") else 400_000
    seed_entity_state(
        pack,
        state,
        epoch=EpochState(epoch_id=11, epoch_start_ms=pack.fixed_now_ms - epoch_age_ms),
    )


def _reload_manager(pack: TierTransitionsFixturePack) -> TierManager:
    return TierManager(pack.workspace, str(pack.db_path))


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    [s for s in load_manager_integration_scenarios() if s.path == "slow"],
    ids=[
        scenario.id for scenario in load_manager_integration_scenarios() if scenario.path == "slow"
    ],
)
def test_manager_slow_epoch_transitions_persist(
    fixture_pack: TierTransitionsFixturePack,
    scenario: ManagerIntegrationScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_scenario(fixture_pack, scenario)
    config = tier_transitions_config(fixture_pack, kind=scenario.kind)
    monkeypatch.setattr("time.time", lambda: fixture_pack.epoch_expired_now_ms / 1000)

    manager = _reload_manager(fixture_pack)
    try:
        expire_epoch_on_manager(manager, now_ms=fixture_pack.epoch_expired_now_ms)
        if scenario.kind == "tool":
            manager.record_tool_candidates([], config=config)
        else:
            manager.record_skill_candidates([], config=config)
        manager.flush_pending()
    finally:
        manager.close()

    reloaded = _reload_manager(fixture_pack)
    try:
        key = (scenario.kind, scenario.entity_id)
        state = reloaded._states.get(key)
        assert state is not None
        assert state.stable_tier == scenario.expected_stable_tier
        assert scenario.expected_reason in latest_epoch_log_reasons(fixture_pack)
    finally:
        reloaded.close()


@pytest.mark.integration
def test_manager_fast_hot_then_epoch_crystallizes_persisted(
    fixture_pack: TierTransitionsFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = next(
        s
        for s in load_manager_integration_scenarios()
        if s.id == "fast_hot_tool_use_then_epoch_crystallize"
    )
    _seed_scenario(fixture_pack, scenario)
    config = tier_transitions_config(fixture_pack)
    tool = tool_dict_for_pack(fixture_pack)
    monkeypatch.setattr("time.time", lambda: fixture_pack.fixed_now_ms / 1000)

    manager = _reload_manager(fixture_pack)
    try:
        manager.record_tool_attempt(tool, config=config, success=True)
        fast_state = manager._states.get(("tool", scenario.entity_id))
        assert fast_state is not None
        assert fast_state.effective_tier == scenario.expected_after_fast_effective_tier
        assert fast_state.stable_tier == scenario.expected_after_fast_stable_tier
        manager.flush_pending()

        expire_epoch_on_manager(manager, now_ms=fixture_pack.epoch_expired_now_ms)
        monkeypatch.setattr("time.time", lambda: fixture_pack.epoch_expired_now_ms / 1000)
        manager.record_tool_candidates([], config=config)
        manager.flush_pending()
    finally:
        manager.close()

    reloaded = _reload_manager(fixture_pack)
    try:
        state = reloaded._states.get(("tool", scenario.entity_id))
        assert state is not None
        assert state.stable_tier == scenario.expected_after_epoch_stable_tier
        assert state.temp_promotion_until_ms is None
        assert scenario.expected_epoch_reason in latest_epoch_log_reasons(fixture_pack)
    finally:
        reloaded.close()

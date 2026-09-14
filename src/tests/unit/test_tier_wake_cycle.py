"""Unit tests for wake-cycle wiring, epoch timing, and fast wake/sleep evaluators."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.tiers.config import TierSectionConfig, tier_section_config
from cyt.tiers.evaluator import epoch_remaining_ms, epoch_ttl_ms
from cyt.tiers.models import EntityTierState, EpochState, Tier
from cyt.tiers.status_overview import format_duration_compact
from cyt.tiers.wake import evaluate_fast_sleep, evaluate_fast_wake
from tests.support.tier_wake_cycle_fixtures import (
    DurationFormatScenario,
    EpochTimingScenario,
    FastSleepScenario,
    FastWakeScenario,
    RequestCycleScenario,
    TierWakeCycleFixturePack,
    epoch_on_disk,
    load_duration_format_scenarios,
    load_epoch_timing_scenarios,
    load_fast_sleep_scenarios,
    load_fast_wake_scenarios,
    load_legacy_config_expectations,
    load_request_cycle_scenarios,
    manager_for_pack,
    materialize_wake_cycle_pack,
    tier_wake_config,
)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    from cyt.tiers.manager import _managers

    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def fixture_pack(tmp_path: Path) -> TierWakeCycleFixturePack:
    return materialize_wake_cycle_pack(tmp_path)


def _wake_cfg(pack: TierWakeCycleFixturePack) -> TierSectionConfig:
    return tier_section_config(tier_wake_config(pack), kind="tool")


def _dormant_state(
    *,
    sleep_cooldown_until_cycle: int = 0,
    stats: dict[str, float] | None = None,
) -> EntityTierState:
    state = EntityTierState(
        entity_id="cyt_mcp:test",
        kind="tool",
        stable_tier=Tier.DORMANT,
        effective_tier=Tier.DORMANT,
        sleep_cooldown_until_cycle=sleep_cooldown_until_cycle,
    )
    if stats:
        for key, value in stats.items():
            setattr(state.stats, key, value)
    return state


def _cold_state(
    *,
    wake_lease_until_cycle: int = 0,
    stats: dict[str, float] | None = None,
) -> EntityTierState:
    state = EntityTierState(
        entity_id="cyt_mcp:test",
        kind="tool",
        stable_tier=Tier.COLD,
        effective_tier=Tier.COLD,
        wake_lease_until_cycle=wake_lease_until_cycle,
    )
    if stats:
        for key, value in stats.items():
            setattr(state.stats, key, value)
    return state


@pytest.mark.parametrize(
    "scenario",
    load_epoch_timing_scenarios(),
    ids=[scenario.id for scenario in load_epoch_timing_scenarios()],
)
def test_epoch_timing_from_fixture(
    scenario: EpochTimingScenario,
    fixture_pack: TierWakeCycleFixturePack,
) -> None:
    cfg = _wake_cfg(fixture_pack)
    epoch = EpochState(
        epoch_start_ms=scenario.epoch_start_ms,
        last_request_ms=scenario.last_request_ms,
    )
    assert epoch_ttl_ms(cfg) == scenario.expected_timeout_ms
    assert (
        epoch_remaining_ms(now_ms=scenario.now_ms, epoch=epoch, cfg=cfg)
        == scenario.expected_remaining_ms
    )


@pytest.mark.parametrize(
    "scenario",
    load_duration_format_scenarios(),
    ids=[scenario.id for scenario in load_duration_format_scenarios()],
)
def test_format_duration_compact_from_fixture(scenario: DurationFormatScenario) -> None:
    assert format_duration_compact(scenario.seconds) == scenario.expected


@pytest.mark.parametrize(
    "scenario",
    load_fast_wake_scenarios(),
    ids=[scenario.id for scenario in load_fast_wake_scenarios()],
)
def test_fast_wake_from_fixture(
    scenario: FastWakeScenario,
    fixture_pack: TierWakeCycleFixturePack,
) -> None:
    cfg = _wake_cfg(fixture_pack)
    state = _dormant_state(
        sleep_cooldown_until_cycle=scenario.sleep_cooldown_until_cycle,
        stats=scenario.stats,
    )
    transition = evaluate_fast_wake(state, cfg=cfg, wake_cycle_id=scenario.wake_cycle_id)
    if scenario.expect_transition:
        assert transition is not None
    else:
        assert transition is None
    assert state.effective_tier == scenario.expected_tier


@pytest.mark.parametrize(
    "scenario",
    load_fast_sleep_scenarios(),
    ids=[scenario.id for scenario in load_fast_sleep_scenarios()],
)
def test_fast_sleep_from_fixture(
    scenario: FastSleepScenario,
    fixture_pack: TierWakeCycleFixturePack,
) -> None:
    cfg = _wake_cfg(fixture_pack)
    state = _cold_state(
        wake_lease_until_cycle=scenario.wake_lease_until_cycle,
        stats=scenario.stats,
    )
    transition = evaluate_fast_sleep(
        state,
        cfg=cfg,
        wake_cycle_id=scenario.wake_cycle_id,
        had_selection=scenario.had_selection,
        had_use=scenario.had_use,
        had_shadow=scenario.had_shadow,
    )
    if scenario.expect_transition:
        assert transition is not None
    else:
        assert transition is None
    assert state.effective_tier == scenario.expected_tier
    if scenario.expected_wake_lease_until_cycle is not None:
        assert state.wake_lease_until_cycle == scenario.expected_wake_lease_until_cycle


@pytest.mark.parametrize(
    "scenario",
    load_request_cycle_scenarios(),
    ids=[scenario.id for scenario in load_request_cycle_scenarios()],
)
def test_request_cycle_from_fixture(
    scenario: RequestCycleScenario,
    fixture_pack: TierWakeCycleFixturePack,
) -> None:
    config = tier_wake_config(fixture_pack)
    manager = manager_for_pack(fixture_pack)
    try:
        if scenario.initial_wake_cycle_id is not None:
            assert manager._epoch.wake_cycle_id == scenario.initial_wake_cycle_id

        manager.begin_request_cycle(config)

        if scenario.expected_after_begin is not None:
            assert manager._epoch.wake_cycle_id == scenario.expected_after_begin

        if scenario.persisted_to_disk:
            on_disk = epoch_on_disk(fixture_pack)
            assert on_disk.wake_cycle_id == scenario.expected_after_begin

        if scenario.entity_id is not None:
            state = manager._states.get(("tool", scenario.entity_id))
            assert state is not None
            assert state.effective_tier == scenario.expected_tier_after_begin
    finally:
        manager.end_request_cycle()
        manager.close()


def test_config_reads_legacy_wake_session_keys(fixture_pack: TierWakeCycleFixturePack) -> None:
    legacy = load_legacy_config_expectations()
    wake_raw = legacy.get("wake")
    assert isinstance(wake_raw, dict)
    cfg = tier_section_config(
        tier_wake_config(fixture_pack, wake_overrides=dict(wake_raw)),
        kind="tool",
    )
    assert cfg.wake_lease_cycles == int(legacy["expected_wake_lease_cycles"])
    assert cfg.sleep_cooldown_cycles == int(legacy["expected_sleep_cooldown_cycles"])

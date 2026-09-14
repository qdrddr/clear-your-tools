"""Fixture-driven unit tests for tier statistics disk persistence."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.tiers.flush_scheduler import (
    reset_tier_flush_scheduler_for_tests,
    start_tier_flush_scheduler,
    stop_tier_flush_scheduler,
)
from cyt.tiers.manager import _managers, flush_all_tier_managers, get_tier_manager
from cyt.tiers.models import EntityKind
from tests.support.tier_flush_fixtures import (
    RecordScenario,
    apply_record_scenario,
    entity_state_on_disk,
    load_flush_config_values,
    load_flush_tool,
    load_record_scenarios,
    manager_for_pack,
    materialize_flush_pack,
    record_scenario_by_id,
    reload_manager_from_disk,
    tier_flush_config,
    warm_tier_statistics,
)


@pytest.fixture(autouse=True)
def _isolated_tier_flush_state() -> Iterator[None]:
    reset_tier_flush_scheduler_for_tests()
    _managers.clear()
    yield
    stop_tier_flush_scheduler()
    reset_tier_flush_scheduler_for_tests()
    _managers.clear()


@pytest.fixture(autouse=True)
def _isolate_master_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: None,
    )


def test_fixture_loader_reads_tool_and_config() -> None:
    tool = load_flush_tool()
    config_values = load_flush_config_values()
    assert tool.entity_id == "cyt_mcp:gitnexus_query"
    assert config_values["deferred_flush_seconds"] == 900.0
    assert config_values["sync_flush_seconds"] == 0.0


def test_seed_tier_flush_db_writes_epoch_and_stats(tmp_path: Path) -> None:
    pack = materialize_flush_pack(tmp_path, seed=True)
    state = entity_state_on_disk(pack)
    assert state is not None
    assert state.stats.attempts == 5.0
    assert state.stats.used == 3.0
    assert state.stats.injected == 2.0

    manager = reload_manager_from_disk(pack)
    try:
        assert manager._epoch.epoch_id == 2
        assert manager._epoch.session_id == 7
    finally:
        manager.close()


@pytest.mark.parametrize(
    "scenario",
    load_record_scenarios(),
    ids=[item.id for item in load_record_scenarios()],
)
def test_record_scenarios_persist_according_to_flush_mode(
    tmp_path: Path,
    scenario: RecordScenario,
) -> None:
    pack = materialize_flush_pack(tmp_path)
    manager = manager_for_pack(pack)
    try:
        apply_record_scenario(manager, pack, scenario)
        state = manager._states.get((EntityKind.TOOL, pack.tool.entity_id))
        assert state is not None
        for key, expected in scenario.expected.items():
            assert getattr(state.stats, key) == expected

        on_disk = entity_state_on_disk(pack)
        if scenario.persisted_before_flush:
            assert on_disk is not None
            for key, expected in scenario.expected.items():
                assert getattr(on_disk.stats, key) == expected
        else:
            assert on_disk is None

        if not scenario.persisted_before_flush:
            assert manager.flush_pending(force=True)
        on_disk_after = entity_state_on_disk(pack)
        if scenario.persisted_after_flush:
            assert on_disk_after is not None
            for key, expected in scenario.expected.items():
                assert getattr(on_disk_after.stats, key) == expected
        else:
            assert on_disk_after is None
    finally:
        manager.close()


def test_startup_manager_loads_seeded_fixture_state(tmp_path: Path) -> None:
    pack = materialize_flush_pack(tmp_path, seed=True)
    manager = get_tier_manager(pack.config, workspace=pack.workspace)
    state = manager._states.get((EntityKind.TOOL, pack.tool.entity_id))
    assert state is not None
    assert state.stats.used == 3.0
    assert manager._epoch.epoch_id == 2


def test_warm_tier_statistics_loads_seeded_state_when_cache_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_flush_pack(tmp_path, seed=True, disk_flush_seconds=900)
    monkeypatch.setattr("cyt.cache.warm.cache_enabled", lambda _cfg: False)

    warm_tier_statistics(pack.config)

    manager = get_tier_manager(pack.config, workspace=pack.workspace)
    state = manager._states.get((EntityKind.TOOL, pack.tool.entity_id))
    assert state is not None
    assert state.stats.attempts == 5.0
    assert state.stats.used == 3.0


def test_get_tier_manager_starts_scheduler_for_deferred_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_flush_pack(tmp_path, disk_flush_seconds=900)
    started: list[dict] = []

    def _capture_start(config: dict) -> None:
        started.append(config)
        stop_tier_flush_scheduler()

    monkeypatch.setattr(
        "cyt.tiers.flush_scheduler.start_tier_flush_scheduler",
        _capture_start,
    )
    get_tier_manager(pack.config, workspace=pack.workspace)
    assert started


def test_flush_all_tier_managers_only_flushes_dirty_managers(tmp_path: Path) -> None:
    pack = materialize_flush_pack(tmp_path, disk_flush_seconds=900)
    manager = get_tier_manager(pack.config, workspace=pack.workspace)
    scenario = record_scenario_by_id("deferred_single_success")
    apply_record_scenario(manager, pack, scenario)

    assert entity_state_on_disk(pack) is None
    assert flush_all_tier_managers(force=False) == 1
    on_disk = entity_state_on_disk(pack)
    assert on_disk is not None
    assert on_disk.stats.used == 1.0
    assert flush_all_tier_managers(force=False) == 0


def test_scheduler_fixture_interval_persists_deferred_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_values = load_flush_config_values()
    interval = config_values["scheduler_flush_seconds"]
    pack = materialize_flush_pack(tmp_path, disk_flush_seconds=interval)
    config = tier_flush_config(pack, disk_flush_seconds=interval)
    monkeypatch.setattr("cyt.tiers.flush_scheduler.tier_disk_flush_seconds", lambda _cfg: interval)

    manager = get_tier_manager(config, workspace=pack.workspace)
    start_tier_flush_scheduler(config)
    scenario = record_scenario_by_id("deferred_single_success")
    apply_record_scenario(manager, pack, scenario)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        on_disk = entity_state_on_disk(pack)
        if on_disk is not None and on_disk.stats.used >= 1.0:
            break
        time.sleep(0.05)
    else:
        pytest.fail("scheduler did not persist deferred tier statistics within timeout")

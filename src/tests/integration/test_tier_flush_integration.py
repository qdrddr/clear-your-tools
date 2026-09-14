"""Integration tests for tier statistics periodic disk persistence."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.tiers.flush_scheduler import (
    reset_tier_flush_scheduler_for_tests,
    stop_tier_flush_scheduler,
)
from cyt.tiers.manager import _managers, flush_all_tier_managers, get_tier_manager
from cyt.tiers.models import EntityKind, EntityTierState
from tests.support.tier_flush_fixtures import (
    IntegrationFlushScenario,
    TierFlushFixturePack,
    entity_state_on_disk,
    http_payload_by_id,
    load_integration_flush_scenarios,
    materialize_flush_pack,
    post_tier_feedback_http,
    warm_tier_statistics,
)


@pytest.fixture(autouse=True)
def _isolated_tier_flush_integration() -> Iterator[None]:
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


def _assert_tool_stats(
    state: EntityTierState,
    expected: dict[str, float | int],
) -> None:
    for key, value in expected.items():
        assert getattr(state.stats, key) == value


def _seeded_manager_state(
    pack: TierFlushFixturePack,
) -> EntityTierState:
    manager = get_tier_manager(pack.config, workspace=pack.workspace)
    state = manager._states.get((EntityKind.TOOL, pack.tool.entity_id))
    assert state is not None
    return state


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario",
    load_integration_flush_scenarios(),
    ids=[item.id for item in load_integration_flush_scenarios()],
)
@pytest.mark.asyncio
async def test_tier_flush_integration_scenarios(
    tmp_path: Path,
    scenario: IntegrationFlushScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_flush_pack(
        tmp_path,
        seed=scenario.uses_seed,
        disk_flush_seconds=scenario.disk_flush_seconds,
    )
    if scenario.disable_cache:
        monkeypatch.setattr("cyt.cache.warm.cache_enabled", lambda _cfg: False)
        warm_tier_statistics(pack.config)
        _assert_tool_stats(_seeded_manager_state(pack), scenario.expected)
        return

    if scenario.uses_seed and scenario.payload_id is None:
        _assert_tool_stats(_seeded_manager_state(pack), scenario.expected)
        return

    assert scenario.payload_id is not None
    payload = http_payload_by_id(scenario.payload_id).payload
    status = await post_tier_feedback_http(
        pack,
        payload,
        disk_flush_seconds=scenario.disk_flush_seconds,
    )
    assert status == 204

    _assert_tool_stats(_seeded_manager_state(pack), scenario.expected)

    on_disk = entity_state_on_disk(pack)
    if scenario.persisted_before_flush is False:
        assert on_disk is None
    elif scenario.persisted_before_flush is True:
        assert on_disk is not None
        _assert_tool_stats(on_disk, scenario.expected)

    if scenario.persisted_after_flush:
        assert flush_all_tier_managers(force=False) >= 0
        on_disk_after = entity_state_on_disk(pack)
        assert on_disk_after is not None
        _assert_tool_stats(on_disk_after, scenario.expected)

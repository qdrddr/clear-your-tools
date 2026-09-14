"""Integration tests for wake-cycle persistence through coordinator, HTTP, and stats."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.pruning.coordinator import CoordinateResult, ToolSource, coordinate_skills_tools_prune
from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers
from tests.support.tier_wake_cycle_fixtures import (
    TierWakeCycleFixturePack,
    WakeCycleIntegrationScenario,
    epoch_on_disk,
    http_payload_by_id,
    load_integration_scenarios,
    manager_for_pack,
    materialize_wake_cycle_pack,
    post_wake_cycle_feedback_http,
    tier_wake_config,
    wake_cycle_tool_dict,
)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture(autouse=True)
def _wake_cycle_isolated_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: None,
    )


@pytest.fixture
def fixture_pack(tmp_path: Path) -> TierWakeCycleFixturePack:
    return materialize_wake_cycle_pack(tmp_path)


@pytest.fixture
def fixed_time(monkeypatch: pytest.MonkeyPatch, fixture_pack: TierWakeCycleFixturePack) -> None:
    now_ms = fixture_pack.fixed_now_ms
    monkeypatch.setattr("time.time", lambda: now_ms / 1000)


def _integration_by_id(scenario_id: str) -> WakeCycleIntegrationScenario:
    for scenario in load_integration_scenarios():
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


@pytest.mark.integration
def test_coordinator_advances_wake_cycle(
    fixture_pack: TierWakeCycleFixturePack,
) -> None:
    scenario = _integration_by_id("coordinator_advances_wake_cycle")
    config = tier_wake_config(fixture_pack)
    assert epoch_on_disk(fixture_pack).wake_cycle_id == scenario.initial_wake_cycle_id

    with (
        patch("cyt.pruning.coordinator.build_prune_plan", return_value=[[]]),
        patch("cyt.pruning.coordinator.run_prune_plan", return_value=CoordinateResult()),
    ):
        coordinate_skills_tools_prune(
            "wake cycle query",
            config,
            [ToolSource("root", [wake_cycle_tool_dict(fixture_pack.tool)])],
            for_hook=True,
            skills_allowed=False,
            tools_allowed=True,
        )

    assert epoch_on_disk(fixture_pack).wake_cycle_id == scenario.expected_after_prune


@pytest.mark.integration
@pytest.mark.asyncio
async def test_http_feedback_does_not_advance_wake_cycle(
    fixture_pack: TierWakeCycleFixturePack,
) -> None:
    scenario = _integration_by_id("http_feedback_no_cycle_advance")
    payload = http_payload_by_id(str(scenario.payload_id)).payload
    assert epoch_on_disk(fixture_pack).wake_cycle_id == scenario.initial_wake_cycle_id

    status = await post_wake_cycle_feedback_http(fixture_pack, payload)
    assert status == 204
    assert epoch_on_disk(fixture_pack).wake_cycle_id == scenario.expected_wake_cycle_id


@pytest.mark.integration
def test_tiers_stats_json_includes_epoch_wake_cycle_fields(
    fixture_pack: TierWakeCycleFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fixed_time: None,
) -> None:
    scenario = _integration_by_id("stats_epoch_fields")
    monkeypatch.chdir(fixture_pack.workspace)

    code = tiers_main(["stats", "--workspace", str(fixture_pack.workspace), "--json"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    epoch = payload["overview"]["epoch"]
    assert epoch["epoch_id"] == scenario.expected_epoch_id
    assert epoch["wake_cycle_id"] == scenario.expected_wake_cycle_id
    assert epoch["epoch_timeout_seconds"] == scenario.expected_timeout_seconds
    assert epoch["epoch_remaining_seconds"] == scenario.expected_remaining_seconds


@pytest.mark.integration
def test_tiers_stats_text_includes_epoch_wake_cycle_lines(
    fixture_pack: TierWakeCycleFixturePack,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fixed_time: None,
) -> None:
    scenario = _integration_by_id("stats_epoch_fields")
    monkeypatch.chdir(fixture_pack.workspace)

    code = tiers_main(["stats", "--workspace", str(fixture_pack.workspace)])
    assert code == 0
    out = capsys.readouterr().out

    assert f"wake_cycle_id: {scenario.expected_wake_cycle_id}" in out
    assert "epoch_timeout: 5:00" in out
    assert "epoch_remaining: 4:00" in out


@pytest.mark.integration
def test_reload_manager_sees_persisted_wake_cycle(
    fixture_pack: TierWakeCycleFixturePack,
) -> None:
    scenario = _integration_by_id("reload_manager_sees_persisted_cycle")
    config = tier_wake_config(fixture_pack)
    manager = manager_for_pack(fixture_pack)
    try:
        assert manager._epoch.wake_cycle_id == scenario.initial_wake_cycle_id
        manager.begin_request_cycle(config)
        assert manager._epoch.wake_cycle_id == scenario.expected_after_begin
    finally:
        manager.end_request_cycle()
        manager.close()

    reloaded = manager_for_pack(fixture_pack)
    try:
        assert reloaded._epoch.wake_cycle_id == scenario.expected_after_begin
    finally:
        reloaded.close()


@pytest.mark.integration
def test_integration_scenario_ids_are_unique() -> None:
    ids = [item.id for item in load_integration_scenarios()]
    assert len(ids) == len(set(ids))

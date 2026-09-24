"""Gherkin steps for tier manager slow epoch and fast/hot transitions."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.models import EpochState, Tier, ToolsTierApplyResult
from cyt.tiers.shadow import schedule_skill_shadow_evaluation, schedule_tool_shadow_evaluation
from tests.support.tier_transitions_fixtures import (
    TierTransitionsFixturePack,
    entity_state_from_scenario,
    expire_epoch_on_manager,
    gherkin_scenario_by_id,
    latest_epoch_log_reasons,
    load_fast_wake_prompts,
    load_mcp_server_tools,
    materialize_transitions_pack,
    mcp_server_tool_dict,
    seed_entity_state,
    skill_metadata_for_pack,
    tier_transitions_config,
    tool_dict_for_pack,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "tier_transitions.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


def _active_scenario(gherkin_context: GherkinContext):
    scenario_id = gherkin_context.payload.get("scenario_id")
    assert isinstance(scenario_id, str)
    return gherkin_scenario_by_id(scenario_id)


def _pack(gherkin_context: GherkinContext) -> TierTransitionsFixturePack:
    pack = gherkin_context.payload.get("pack")
    assert isinstance(pack, TierTransitionsFixturePack)
    return pack


def _manager(gherkin_context: GherkinContext) -> TierManager:
    manager = gherkin_context.payload.get("manager")
    assert isinstance(manager, TierManager)
    return manager


def _setup_pack(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    scenario_id: str,
    *,
    seed: bool = True,
) -> None:
    _managers.clear()
    pack = materialize_transitions_pack(tmp_path)
    scenario = gherkin_scenario_by_id(scenario_id)
    gherkin_context.tmp_path = pack.workspace
    gherkin_context.payload = {
        "pack": pack,
        "scenario_id": scenario_id,
        "scenario": scenario,
    }
    if seed:
        state = entity_state_from_scenario(
            kind=scenario.kind,
            entity_id=scenario.entity_id,
            stable_tier=scenario.seed_tier,
            stats=scenario.stats,
        )
        # Fast/hot paths must start inside a live epoch so use does not crystallize immediately.
        epoch_age_ms = 60_000 if scenario.path.startswith("fast") and scenario.path != "fast_wake" else 400_000
        seed_entity_state(
            pack,
            state,
            epoch=EpochState(epoch_id=5, epoch_start_ms=pack.fixed_now_ms - epoch_age_ms),
        )
    manager = TierManager(pack.workspace, str(pack.db_path))
    gherkin_context.payload["manager"] = manager
    gherkin_context.config = tier_transitions_config(pack, kind=scenario.kind)


def _setup_fast_wake_pack(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    scenario_id: str,
) -> None:
    _managers.clear()
    pack = materialize_transitions_pack(tmp_path)
    scenario = gherkin_scenario_by_id(scenario_id)
    gherkin_context.tmp_path = pack.workspace
    gherkin_context.payload = {
        "pack": pack,
        "scenario_id": scenario_id,
        "scenario": scenario,
    }
    epoch = EpochState(epoch_id=5, epoch_start_ms=pack.fixed_now_ms - 400_000)
    if scenario.mcp_server_batch:
        entity_ids = [tool.entity_id for tool in load_mcp_server_tools()]
        gherkin_context.payload["mcp_server_entity_ids"] = entity_ids
        for entity_id in entity_ids:
            seed_entity_state(
                pack,
                entity_state_from_scenario(
                    kind="tool",
                    entity_id=entity_id,
                    stable_tier=Tier.DORMANT,
                    stats=scenario.stats,
                ),
                epoch=epoch,
            )
    else:
        seed_entity_state(
            pack,
            entity_state_from_scenario(
                kind=scenario.kind,
                entity_id=scenario.entity_id,
                stable_tier=scenario.seed_tier,
                stats=scenario.stats,
            ),
            epoch=epoch,
        )
    manager = TierManager(pack.workspace, str(pack.db_path))
    gherkin_context.payload["manager"] = manager
    gherkin_context.config = tier_transitions_config(pack, kind=scenario.kind)


@given("tier transition fixtures for slow tool demotion")
def given_slow_tool_demotion(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_pack(gherkin_context, tmp_path, "slow_tool_demotion")


@given("tier transition fixtures for slow tool promotion")
def given_slow_tool_promotion(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_pack(gherkin_context, tmp_path, "slow_tool_promotion")


@given("tier transition fixtures for slow skill promotion")
def given_slow_skill_promotion(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_pack(gherkin_context, tmp_path, "slow_skill_promotion")


@given("tier transition fixtures for slow skill demotion")
def given_slow_skill_demotion(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_pack(gherkin_context, tmp_path, "slow_skill_demotion")


@given("tier transition fixtures for fast hot tool jump")
def given_fast_hot_tool_jump(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_pack(gherkin_context, tmp_path, "fast_hot_tool_jump", seed=True)


@given("tier transition fixtures for fast hot epoch crystallize")
def given_fast_hot_epoch_crystallize(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_pack(gherkin_context, tmp_path, "fast_hot_epoch_crystallize", seed=True)


@given("tier transition fixtures for fast hot skill jump")
def given_fast_hot_skill_jump(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_pack(gherkin_context, tmp_path, "fast_hot_skill_jump", seed=True)


@given("tier transition fixtures for fast hot skill epoch crystallize")
def given_fast_hot_skill_epoch_crystallize(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_pack(gherkin_context, tmp_path, "fast_hot_skill_epoch_crystallize", seed=True)


@given("tier transition fixtures for fast wake tool from prompt")
def given_fast_wake_tool_from_prompt(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_fast_wake_pack(gherkin_context, tmp_path, "fast_wake_tool_from_prompt")


@given("tier transition fixtures for fast wake skill from prompt")
def given_fast_wake_skill_from_prompt(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_fast_wake_pack(gherkin_context, tmp_path, "fast_wake_skill_from_prompt")


@given("tier transition fixtures for fast wake MCP server batch")
def given_fast_wake_mcp_server_batch(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    _setup_fast_wake_pack(gherkin_context, tmp_path, "fast_wake_mcp_server_batch")


@when("the tier manager processes the last user prompt in the background for tools")
def when_background_prompt_tools(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _pack(gherkin_context)
    manager = _manager(gherkin_context)
    scenario = _active_scenario(gherkin_context)
    prompt_key = scenario.user_prompt_key or "tool"
    prompt = load_fast_wake_prompts()[prompt_key]
    config = tier_transitions_config(pack, kind="tool")

    submitted: list[bool] = []

    def _sync_submit(fn: object) -> None:
        submitted.append(True)
        assert callable(fn)
        fn()

    monkeypatch.setattr("cyt.tiers.shadow._executor.submit", lambda fn: _sync_submit(fn))
    manager.begin_request_cycle(config)

    if scenario.mcp_server_batch:
        tools = [mcp_server_tool_dict(tool) for tool in load_mcp_server_tools()]
        dormant_ids = [tool.entity_id for tool in load_mcp_server_tools()]
    else:
        tools = [tool_dict_for_pack(pack)]
        dormant_ids = [scenario.entity_id]

    tier_apply = ToolsTierApplyResult(
        eligible_tools=[],
        t4_direct=[],
        excluded_t0=dormant_ids,
        policy_overrides={},
        tier_by_tool={entity_id: Tier.DORMANT for entity_id in dormant_ids},
    )
    schedule_tool_shadow_evaluation(
        config=config,
        query=prompt,
        original_tools=tools,
        tier_apply=tier_apply,
        manager=manager,
    )
    assert submitted, "shadow evaluation should be submitted to the background executor"
    manager.flush_pending()
    gherkin_context.payload["entity_id"] = scenario.entity_id
    gherkin_context.payload["kind"] = "tool"


@when("the tier manager processes the last user prompt in the background for skills")
def when_background_prompt_skills(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _pack(gherkin_context)
    manager = _manager(gherkin_context)
    scenario = _active_scenario(gherkin_context)
    prompt_key = scenario.user_prompt_key or "skill"
    prompt = load_fast_wake_prompts()[prompt_key]
    config = tier_transitions_config(pack, kind="skill")

    submitted: list[bool] = []

    def _sync_submit(fn: object) -> None:
        submitted.append(True)
        assert callable(fn)
        fn()

    monkeypatch.setattr("cyt.tiers.shadow._executor.submit", lambda fn: _sync_submit(fn))
    manager.begin_request_cycle(config)

    entity_id = scenario.entity_id
    schedule_skill_shadow_evaluation(
        config=config,
        query=prompt,
        dormant_entity_ids=[entity_id],
        skills_by_id={entity_id: skill_metadata_for_pack(pack)},
        manager=manager,
    )
    assert submitted, "shadow evaluation should be submitted to the background executor"
    manager.flush_pending()
    gherkin_context.payload["entity_id"] = entity_id
    gherkin_context.payload["kind"] = "skill"


@when("the tier manager runs an expired epoch for tools")
def when_expired_epoch_tools(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _pack(gherkin_context)
    manager = _manager(gherkin_context)
    scenario = _active_scenario(gherkin_context)
    config = tier_transitions_config(pack, kind="tool")
    monkeypatch.setattr("time.time", lambda: pack.epoch_expired_now_ms / 1000)
    expire_epoch_on_manager(manager, now_ms=pack.epoch_expired_now_ms)
    manager.record_tool_candidates([], config=config)
    manager.flush_pending()
    gherkin_context.payload["entity_id"] = scenario.entity_id
    gherkin_context.payload["kind"] = "tool"


@when("the tier manager runs an expired epoch for skills")
def when_expired_epoch_skills(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _pack(gherkin_context)
    manager = _manager(gherkin_context)
    scenario = _active_scenario(gherkin_context)
    config = tier_transitions_config(pack, kind="skill")
    monkeypatch.setattr("time.time", lambda: pack.epoch_expired_now_ms / 1000)
    expire_epoch_on_manager(manager, now_ms=pack.epoch_expired_now_ms)
    manager.record_skill_candidates([], config=config)
    manager.flush_pending()
    gherkin_context.payload["entity_id"] = scenario.entity_id
    gherkin_context.payload["kind"] = "skill"


@when("the tier manager records a successful tool use")
def when_successful_tool_use(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _pack(gherkin_context)
    manager = _manager(gherkin_context)
    scenario = _active_scenario(gherkin_context)
    config = tier_transitions_config(pack)
    monkeypatch.setattr("time.time", lambda: pack.fixed_now_ms / 1000)
    manager.record_tool_attempt(tool_dict_for_pack(pack), config=config, success=True)
    manager.flush_pending()
    gherkin_context.payload["entity_id"] = scenario.entity_id
    gherkin_context.payload["kind"] = "tool"


@when("the tier manager records a successful skill use")
def when_successful_skill_use(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _pack(gherkin_context)
    manager = _manager(gherkin_context)
    scenario = _active_scenario(gherkin_context)
    config = tier_transitions_config(pack, kind="skill")
    monkeypatch.setattr("time.time", lambda: pack.fixed_now_ms / 1000)
    manager.record_skill_used(scenario.entity_id, config=config)
    manager.flush_pending()
    gherkin_context.payload["entity_id"] = scenario.entity_id
    gherkin_context.payload["kind"] = "skill"


@then("the tool stable tier should be COLD")
def then_tool_stable_cold(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("tool", entity_id))
    assert state is not None
    assert state.stable_tier == Tier.COLD


@then("the tool stable tier should be ACTIVE")
def then_tool_stable_active(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("tool", entity_id))
    assert state is not None
    assert state.stable_tier == Tier.ACTIVE


@then("the skill stable tier should be ACTIVE")
def then_skill_stable_active(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("skill", entity_id))
    assert state is not None
    assert state.stable_tier == Tier.ACTIVE


@then("the skill stable tier should be COLD")
def then_skill_stable_cold(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("skill", entity_id))
    assert state is not None
    assert state.stable_tier == Tier.COLD


@then("the epoch log should include slow_demote_t2_t1_unused")
def then_epoch_log_demote_unused(gherkin_context: GherkinContext) -> None:
    assert "slow_demote_t2_t1_unused" in latest_epoch_log_reasons(_pack(gherkin_context))


@then("the epoch log should include slow_promote_t1_t2")
def then_epoch_log_promote_t1_t2(gherkin_context: GherkinContext) -> None:
    assert "slow_promote_t1_t2" in latest_epoch_log_reasons(_pack(gherkin_context))


@then("the tool effective tier should be HOT")
def then_tool_effective_hot(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("tool", entity_id))
    assert state is not None
    assert state.effective_tier == Tier.HOT


@then("the skill effective tier should be HOT")
def then_skill_effective_hot(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("skill", entity_id))
    assert state is not None
    assert state.effective_tier == Tier.HOT


@then("the tool stable tier should remain COLD")
def then_tool_stable_remains_cold(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("tool", entity_id))
    assert state is not None
    assert state.stable_tier == Tier.COLD


@then("the skill stable tier should remain COLD")
def then_skill_stable_remains_cold(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("skill", entity_id))
    assert state is not None
    assert state.stable_tier == Tier.COLD


@then("the tool should have a temporary promotion expiry")
def then_tool_temp_promotion(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("tool", entity_id))
    assert state is not None
    assert state.temp_promotion_until_ms is not None


@then("the skill should have a temporary promotion expiry")
def then_skill_temp_promotion(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("skill", entity_id))
    assert state is not None
    assert state.temp_promotion_until_ms is not None


@then("the tool stable tier should be HOT")
def then_tool_stable_hot(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("tool", entity_id))
    assert state is not None
    assert state.stable_tier == Tier.HOT


@then("the skill stable tier should be HOT")
def then_skill_stable_hot(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("skill", entity_id))
    assert state is not None
    assert state.stable_tier == Tier.HOT


@then("the tool temporary promotion should be cleared")
def then_tool_temp_cleared(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("tool", entity_id))
    assert state is not None
    assert state.temp_promotion_until_ms is None


@then("the skill temporary promotion should be cleared")
def then_skill_temp_cleared(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get(("skill", entity_id))
    assert state is not None
    assert state.temp_promotion_until_ms is None


@then("the entity should have a wake lease from fast promotion")
def then_entity_wake_lease(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    kind = str(gherkin_context.payload["kind"])
    entity_id = str(gherkin_context.payload["entity_id"])
    state = manager._states.get((kind, entity_id))
    assert state is not None
    assert state.wake_lease_until_cycle > 0


@then("every dormant tool on the MCP server should be COLD")
def then_all_mcp_server_tools_cold(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_ids = gherkin_context.payload.get("mcp_server_entity_ids", [])
    assert entity_ids
    for entity_id in entity_ids:
        state = manager._states.get(("tool", str(entity_id)))
        assert state is not None
        assert state.stable_tier == Tier.COLD


@then("every MCP server tool should have a wake lease from fast promotion")
def then_all_mcp_server_wake_lease(gherkin_context: GherkinContext) -> None:
    manager = _manager(gherkin_context)
    entity_ids = gherkin_context.payload.get("mcp_server_entity_ids", [])
    assert entity_ids
    for entity_id in entity_ids:
        state = manager._states.get(("tool", str(entity_id)))
        assert state is not None
        assert state.wake_lease_until_cycle > 0


@pytest.fixture(autouse=True)
def _close_manager(gherkin_context: GherkinContext) -> None:
    yield
    manager = gherkin_context.payload.get("manager")
    if isinstance(manager, TierManager):
        manager.close()
    _managers.clear()

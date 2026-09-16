"""Unit tests for tier manager and adapters."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from cyt.tiers.adapters.tools import (
    apply_tool_tiers,
    merge_t4_tools,
    stamp_tool_injection_tiers,
    tool_entity_id,
)
from cyt.tiers.config import tier_section_config
from cyt.tiers.evaluator import (
    epoch_boundary,
    epoch_remaining_ms,
    epoch_ttl_ms,
    evaluate_slow_clock,
)
from cyt.tiers.manager import TierManager
from cyt.tiers.models import EffectiveStats, EntityTierState, EpochState, Tier, TierProject
from cyt.tiers.store import TierStore
from cyt.tiers.wake import evaluate_fast_wake


@pytest.fixture
def tier_db(tmp_path: Path) -> str:
    return str(tmp_path / "tier_state.db")


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def manager(project_root: Path, tier_db: str) -> TierManager:
    return TierManager(project_root, tier_db)


@pytest.fixture(autouse=True)
def _tier_manager_isolated_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: None,
    )


def test_tool_entity_id_uses_catalog_source() -> None:
    tool = {"name": "search", "cyt_catalog_source": "cyt_mcp"}
    assert tool_entity_id(tool) == "cyt_mcp:search"


def test_tool_entity_id_infers_mcpc_wire_name() -> None:
    tool = {
        "name": "@fff/grep",
        "tool_name": "grep",
        "mcpc_session": "@fff",
    }
    assert tool_entity_id(tool) == "mcpc:@fff/grep"


def test_tool_entity_id_infers_mcpc_from_at_slash_name() -> None:
    tool = {"name": "@ctx7/resolve-library-id"}
    assert tool_entity_id(tool) == "mcpc:@ctx7/resolve-library-id"


def test_normalize_tool_entity_states_merges_unknown_rows() -> None:
    from cyt.tiers.adapters.tools import normalize_tool_entity_states
    from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState

    states: dict[tuple[str, str], EntityTierState] = {
        (EntityKind.TOOL, "unknown:@fff/grep"): EntityTierState(
            entity_id="unknown:@fff/grep",
            kind=EntityKind.TOOL,
            stats=EffectiveStats(injected=10.0),
        ),
    }
    removed, updated = normalize_tool_entity_states(states)
    assert removed == ["unknown:@fff/grep"]
    assert len(updated) == 1
    assert updated[0].entity_id == "mcpc:@fff/grep"
    assert states[("tool", "mcpc:@fff/grep")].stats.injected == 10.0
    assert ("tool", "unknown:@fff/grep") not in states


def test_prepare_tool_for_tier_pipeline_strips_schema_by_tier() -> None:
    from cyt.tiers.adapters.tools import prepare_tool_for_tier_pipeline
    from cyt.tiers.models import Tier

    tool = {
        "name": "demo",
        "description": "Demo tool",
        "input_schema": {
            "type": "object",
            "properties": {
                "required_arg": {"type": "string"},
                "optional_arg": {"type": "string"},
            },
            "required": ["required_arg"],
        },
    }
    t1 = prepare_tool_for_tier_pipeline(tool, Tier.COLD)
    assert t1.get("input_schema") == {}
    t2 = prepare_tool_for_tier_pipeline(tool, Tier.ACTIVE)
    assert list(t2["input_schema"]["properties"].keys()) == ["required_arg"]
    t3 = prepare_tool_for_tier_pipeline(tool, Tier.HOT)
    assert set(t3["input_schema"]["properties"].keys()) == {"required_arg", "optional_arg"}


def test_apply_tool_tiers_excludes_dormant_from_bm25_pool(config_with_tiers_enabled: dict) -> None:
    tools = [
        {"name": "a", "cyt_catalog_source": "cyt_mcp", "description": "A"},
        {"name": "b", "cyt_catalog_source": "cyt_mcp", "description": "B"},
    ]
    tier_map = {
        tool_entity_id(tools[0]): Tier.DORMANT,
        tool_entity_id(tools[1]): Tier.ACTIVE,
    }
    result = apply_tool_tiers(tools, tier_for_tool=tier_map, apply=True)
    assert {tool["name"] for tool in result.eligible_tools} == {"b"}
    assert tool_entity_id(tools[0]) in result.excluded_t0
    assert "a" not in result.policy_overrides
    assert result.policy_overrides["b"] == "tier_active"


def test_apply_tool_tiers_shadow_does_not_filter(config_with_tiers_shadow: dict) -> None:
    tools = [{"name": "a", "cyt_catalog_source": "cyt_mcp"}]
    tier_map = {tool_entity_id(tools[0]): Tier.DORMANT}
    result = apply_tool_tiers(tools, tier_for_tool=tier_map, apply=False)
    assert len(result.eligible_tools) == 1


def test_merge_t4_tools_overrides_pruned() -> None:
    pruned = [{"name": "hot", "description": "small"}]
    t4 = [{"name": "hot", "description": "full", "input_schema": {"type": "object"}}]
    merged = merge_t4_tools(pruned, t4)
    assert merged[0]["input_schema"]["type"] == "object"


def test_stamp_tool_injection_tiers_marks_t4_and_t2() -> None:
    tool = {"name": "demo", "cyt_catalog_source": "cyt_mcp", "description": "demo"}
    entity_id = tool_entity_id(tool)
    tier_map = {entity_id: Tier.ACTIVE}
    stamped = stamp_tool_injection_tiers([tool], tier_map, t4_names={"other"})
    assert stamped[0]["cyt_injection_tier"] == "t2"
    stamped_t4 = stamp_tool_injection_tiers([tool], tier_map, t4_names={"demo"})
    assert stamped_t4[0]["cyt_injection_tier"] == "t4"


def test_tier_store_roundtrip(project_root: Path, tier_db: str) -> None:
    store = TierStore.open(tier_db)
    try:
        project_id = store.get_or_create_project(str(project_root))
        project = TierProject(project_id=project_id, root_path=project_root)
        state = EntityTierState(
            entity_id="cyt_mcp:search",
            kind="tool",
            stable_tier=Tier.HOT,
            effective_tier=Tier.HOT,
            stats=EffectiveStats(candidates=3, injected=2, used=1),
        )
        store.upsert_entity_state(project, state)
        loaded = store.load_entity_states(project)
        assert loaded[("tool", "cyt_mcp:search")].stable_tier == Tier.HOT
        assert loaded[("tool", "cyt_mcp:search")].stats.used == 1.0
    finally:
        store.close()


def test_record_tool_candidates_increments_stats(manager: TierManager, base_config: dict) -> None:
    cfg = {
        **base_config,
        "tools": {**base_config.get("tools", {}), "tiers": {"mode": "shadow"}},
    }
    tools = [{"name": "x", "cyt_catalog_source": "cyt_mcp"}]
    manager.record_tool_candidates(tools, cfg)
    state = manager._states[("tool", "cyt_mcp:x")]
    assert state.stats.candidates >= 1.0


def _cyt_mcp_only_config(base_config: dict) -> dict:
    tools = dict(base_config.get("tools") or {})
    hook = dict(tools.get("hook") or {})
    hook["tools_from"] = ["cyt_mcp"]
    tools["hook"] = hook
    tools["tiers"] = {"mode": "shadow"}
    return {**base_config, "tools": tools}


def test_record_tool_candidates_skips_non_configured_catalog_source(
    manager: TierManager,
    base_config: dict,
) -> None:
    cfg = _cyt_mcp_only_config(base_config)
    manager.record_tool_candidates(
        [
            {"name": "search", "cyt_catalog_source": "cyt_mcp"},
            {"name": "@fff/grep", "mcpc_session": "@fff"},
            {"name": "tools.demo.tool", "cyt_catalog_source": "definitions"},
        ],
        cfg,
    )
    assert ("tool", "cyt_mcp:search") in manager._states
    assert ("tool", "mcpc:@fff/grep") not in manager._states
    assert ("tool", "definitions:tools.demo.tool") not in manager._states


def test_purge_inactive_tool_sources_removes_stale_mcpc_rows(
    manager: TierManager,
    base_config: dict,
) -> None:
    cfg = _cyt_mcp_only_config(base_config)
    grep_state = manager._ensure_state("tool", "mcpc:@fff/grep")
    search_state = manager._ensure_state("tool", "cyt_mcp:search")
    assert grep_state is not None
    assert search_state is not None
    manager._states[("tool", "mcpc:@fff/grep")] = grep_state
    manager._states[("tool", "cyt_mcp:search")] = search_state
    manager.purge_inactive_tool_sources(cfg)
    assert ("tool", "mcpc:@fff/grep") not in manager._states
    assert ("tool", "cyt_mcp:search") in manager._states


def test_purge_removes_test_fixture_tools_not_in_loaded_catalog(
    manager: TierManager,
    base_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cyt_mcp_only_config(base_config)
    catalog = [{"name": "codebase-memory_search_graph", "cyt_catalog_source": "cyt_mcp"}]
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda _config, blocking=False: catalog,
    )
    stale_state = manager._ensure_state("tool", "cyt_mcp:mcp__x__tool")
    active_state = manager._ensure_state("tool", "cyt_mcp:codebase-memory_search_graph")
    assert stale_state is not None
    assert active_state is not None
    manager._states[("tool", "cyt_mcp:mcp__x__tool")] = stale_state
    manager._states[("tool", "cyt_mcp:codebase-memory_search_graph")] = active_state
    manager.purge_inactive_tool_sources(cfg)
    assert ("tool", "cyt_mcp:mcp__x__tool") not in manager._states
    assert ("tool", "cyt_mcp:codebase-memory_search_graph") in manager._states


def test_fast_wake_from_dormant(manager: TierManager, base_config: dict) -> None:
    cfg = tier_section_config(
        {**base_config, "tools": {"tiers": {"mode": "shadow"}}},
        kind="tool",
    )
    state = EntityTierState(
        entity_id="cyt_mcp:x",
        kind="tool",
        stable_tier=Tier.DORMANT,
        effective_tier=Tier.DORMANT,
        stats=EffectiveStats(used_without_injection=1),
    )
    transition = evaluate_fast_wake(state, cfg=cfg, wake_cycle_id=1)
    assert transition is not None
    assert state.effective_tier == Tier.COLD


def test_epoch_boundary_idle_gap() -> None:
    cfg = tier_section_config(
        {"tools": {"tiers": {"cache_epoch": {"prompt_cache_ttl_minutes": 5}}}},
        kind="tool",
    )
    epoch = EpochState(epoch_start_ms=0, last_request_ms=0)
    assert epoch_boundary(now_ms=6 * 60 * 1000, epoch=epoch, cfg=cfg)


def test_slow_clock_promotion() -> None:
    cfg = tier_section_config(
        {"tools": {"tiers": {"evaluation": {"min_injections_before_reconsider": 2}}}},
        kind="tool",
    )
    state = EntityTierState(
        entity_id="tool:a",
        kind="tool",
        stable_tier=Tier.COLD,
        effective_tier=Tier.COLD,
        stats=EffectiveStats(candidates=500, injected=400, used=200),
    )
    transitions = evaluate_slow_clock({("tool", "tool:a"): state}, cfg=cfg, epoch=EpochState())
    assert transitions
    assert state.stable_tier == Tier.ACTIVE


def test_epoch_crystallizes_successful_tool_use_before_temp_expiry() -> None:
    from cyt.tiers.evaluator import crystallize_successful_tool_promotions_at_epoch

    future_ms = 9_999_999_999_999
    state = EntityTierState(
        entity_id="cyt_mcp:gitnexus_query",
        kind="tool",
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.HOT,
        overlap_tier=Tier.ACTIVE,
        temp_promotion_until_ms=future_ms,
        stats=EffectiveStats(
            used=3.0,
            attempts=4.0,
            injected=5.0,
            candidates=7.0,
            epoch_used=1.0,
            epoch_attempts=1.0,
        ),
    )
    transitions = crystallize_successful_tool_promotions_at_epoch(
        {("tool", state.entity_id): state},
        epoch=EpochState(epoch_id=7),
    )
    assert state.stable_tier == Tier.HOT
    assert state.effective_tier == Tier.HOT
    assert state.temp_promotion_until_ms is None
    assert state.overlap_tier is None
    assert transitions
    assert transitions[0].reason == "slow_promote_epoch_crystallized"
    assert transitions[0].temporary is False


def test_fast_promote_on_tool_use_jumps_cold_tool_to_hot() -> None:
    from cyt.tiers.wake import fast_promote_on_tool_use

    state = EntityTierState(
        entity_id="cyt_mcp:codebase-memory_search_graph",
        kind="tool",
        stable_tier=Tier.COLD,
        effective_tier=Tier.COLD,
        stats=EffectiveStats(used=1.0, attempts=1.0),
    )
    transition = fast_promote_on_tool_use(state, cfg=None)
    assert transition is not None
    assert state.effective_tier == Tier.HOT
    assert state.stable_tier == Tier.COLD
    assert state.temp_promotion_until_ms is not None


def test_fast_promote_on_tool_use_does_not_retemp_stable_hot_tools() -> None:
    from cyt.tiers.wake import fast_promote_on_tool_use

    state = EntityTierState(
        entity_id="cyt_mcp:gitnexus_query",
        kind="tool",
        stable_tier=Tier.HOT,
        effective_tier=Tier.HOT,
        stats=EffectiveStats(used=5.0, attempts=5.0),
    )
    assert fast_promote_on_tool_use(state, cfg=None) is None
    assert state.temp_promotion_until_ms is None
    assert state.overlap_tier is None


def test_epoch_crystallizes_despite_poor_lifetime_execution() -> None:
    from cyt.tiers.evaluator import (
        crystallize_successful_tool_promotions_at_epoch,
        evaluate_slow_clock,
    )

    state = EntityTierState(
        entity_id="cyt_mcp:codebase-memory_search_graph",
        kind="tool",
        stable_tier=Tier.COLD,
        effective_tier=Tier.ACTIVE,
        stats=EffectiveStats(
            used=3.0,
            attempts=11.0,
            injected=8.0,
            candidates=13.0,
            epoch_used=1.0,
            epoch_attempts=3.0,
        ),
    )
    key = ("tool", state.entity_id)
    transitions = crystallize_successful_tool_promotions_at_epoch(
        {key: state},
        epoch=EpochState(epoch_id=9),
    )
    assert state.stable_tier == Tier.HOT
    assert transitions[0].reason == "slow_promote_epoch_crystallized"
    demotions = evaluate_slow_clock(
        {key: state},
        cfg=tier_section_config({}, kind="tool"),
        epoch=EpochState(),
    )
    assert not demotions


def test_epoch_success_blocks_demotion_of_stable_hot_tool() -> None:
    from cyt.tiers.evaluator import evaluate_slow_clock

    state = EntityTierState(
        entity_id="cyt_mcp:gitnexus_query",
        kind="tool",
        stable_tier=Tier.HOT,
        effective_tier=Tier.HOT,
        stats=EffectiveStats(
            injected=8.0,
            candidates=13.0,
            used=6.0,
            attempts=7.0,
            epoch_used=1.0,
            epoch_attempts=1.0,
        ),
    )
    cfg = tier_section_config({}, kind="tool")
    transitions = evaluate_slow_clock(
        {("tool", state.entity_id): state},
        cfg=cfg,
        epoch=EpochState(),
    )
    assert transitions == []
    assert state.stable_tier == Tier.HOT


def test_epoch_success_allows_t3_t4_promotion_for_heavily_used_tool() -> None:
    from cyt.tiers.evaluator import evaluate_slow_clock

    state = EntityTierState(
        entity_id="cyt_mcp:semble_search",
        kind="tool",
        stable_tier=Tier.HOT,
        effective_tier=Tier.HOT,
        stats=EffectiveStats(
            injected=20.0,
            candidates=45.0,
            used=26.0,
            attempts=28.0,
            epoch_used=1.0,
            epoch_attempts=1.0,
        ),
    )
    cfg = tier_section_config({}, kind="tool")
    transitions = evaluate_slow_clock(
        {("tool", state.entity_id): state},
        cfg=cfg,
        epoch=EpochState(),
    )
    assert any(t.reason == "slow_promote_t3_t4" for t in transitions)
    assert state.stable_tier == Tier.EXTRA_HOT


def test_t4_promotion_uses_execution_not_injection_demand() -> None:
    from cyt.tiers.evaluator import evaluate_slow_clock
    from cyt.tiers.scores import demand_score, execution_score

    stats = EffectiveStats(
        injected=20.0,
        candidates=45.0,
        used=26.0,
        attempts=28.0,
    )
    cfg = tier_section_config({}, kind="tool")
    assert demand_score(stats) < cfg.thresholds_t34.promote_demand
    assert execution_score(stats) >= cfg.thresholds_t34.promote_demand

    state = EntityTierState(
        entity_id="cyt_mcp:fff_grep",
        kind="tool",
        stable_tier=Tier.HOT,
        effective_tier=Tier.HOT,
        stats=stats,
    )
    transitions = evaluate_slow_clock(
        {("tool", state.entity_id): state},
        cfg=cfg,
        epoch=EpochState(),
    )
    assert any(t.reason == "slow_promote_t3_t4" for t in transitions)
    assert state.stable_tier == Tier.EXTRA_HOT


def test_expire_temporary_promotions_crystallizes_successful_tool_use() -> None:
    from cyt.tiers.evaluator import expire_temporary_promotions

    state = EntityTierState(
        entity_id="cyt_mcp:gitnexus_query",
        kind="tool",
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.HOT,
        overlap_tier=Tier.ACTIVE,
        temp_promotion_until_ms=1,
        stats=EffectiveStats(
            used=3.0,
            attempts=4.0,
            injected=5.0,
            candidates=7.0,
            epoch_used=1.0,
            epoch_attempts=1.0,
        ),
    )
    transitions = expire_temporary_promotions({("tool", state.entity_id): state}, now_ms=2)
    assert state.stable_tier == Tier.HOT
    assert state.effective_tier == Tier.HOT
    assert state.temp_promotion_until_ms is None
    assert transitions
    assert transitions[0].reason == "slow_promote_temp_crystallized"
    assert transitions[0].temporary is False


def test_expire_temporary_promotions_reverts_unused_tool_temp_promotion() -> None:
    from cyt.tiers.evaluator import expire_temporary_promotions

    state = EntityTierState(
        entity_id="cyt_mcp:unused_tool",
        kind="tool",
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.HOT,
        overlap_tier=Tier.ACTIVE,
        temp_promotion_until_ms=1,
        stats=EffectiveStats(injected=2.0, candidates=7.0),
    )
    transitions = expire_temporary_promotions({("tool", state.entity_id): state}, now_ms=2)
    assert state.stable_tier == Tier.ACTIVE
    assert state.effective_tier == Tier.ACTIVE
    assert transitions
    assert transitions[0].reason == "temp_promotion_expired"


def test_slow_clock_demotes_t2_without_injection_when_exposure_high() -> None:
    cfg = tier_section_config(
        {"tools": {"tiers": {"evaluation": {"min_injections_before_reconsider": 8}}}},
        kind="tool",
    )
    state = EntityTierState(
        entity_id="tool:unused",
        kind="tool",
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.ACTIVE,
        stats=EffectiveStats(candidates=20, injected=0, used=0),
    )
    transitions = evaluate_slow_clock({("tool", "tool:unused"): state}, cfg=cfg, epoch=EpochState())
    assert transitions
    assert any(t.reason == "slow_demote_t2_t1" for t in transitions)
    assert state.stable_tier == Tier.COLD


@pytest.fixture
def base_config() -> dict:
    from cyt.config import load_config

    return load_config()


@pytest.fixture
def config_with_tiers_enabled(base_config: dict) -> dict:
    tools = dict(base_config.get("tools") or {})
    tools["tiers"] = {"mode": "live"}
    return {**base_config, "tools": tools}


@pytest.fixture
def config_with_tiers_shadow(base_config: dict) -> dict:
    tools = dict(base_config.get("tools") or {})
    tools["tiers"] = {"mode": "shadow"}
    return {**base_config, "tools": tools}


def test_record_tool_attempt_increments_attempts_and_used_on_success(
    manager: TierManager,
    config_with_tiers_shadow: dict,
) -> None:
    tool = {"name": "search", "cyt_catalog_source": "cyt_mcp"}
    manager.record_tool_attempt(tool, config=config_with_tiers_shadow, success=True)
    state = manager._states.get(("tool", "cyt_mcp:search"))
    assert state is not None
    assert state.stats.attempts == 1.0
    assert state.stats.used == 1.0


def test_record_tool_attempt_failure_increments_attempts_only(
    manager: TierManager,
    config_with_tiers_shadow: dict,
) -> None:
    tool = {"name": "search", "cyt_catalog_source": "cyt_mcp"}
    manager.record_tool_attempt(tool, config=config_with_tiers_shadow, success=False)
    state = manager._states.get(("tool", "cyt_mcp:search"))
    assert state is not None
    assert state.stats.attempts == 1.0
    assert state.stats.used == 0.0


def test_execution_score_uses_attempts_denominator() -> None:
    from cyt.tiers.scores import execution_score, utility_score

    stats = EffectiveStats(injected=10.0, used=5.0, attempts=20.0)
    assert execution_score(stats) < utility_score(stats)


def test_begin_request_cycle_increments_wake_cycle_id(
    manager: TierManager,
    config_with_tiers_shadow: dict,
) -> None:
    initial = manager._epoch.wake_cycle_id
    manager.begin_request_cycle(config_with_tiers_shadow)
    assert manager._epoch.wake_cycle_id == initial + 1
    manager.begin_request_cycle(config_with_tiers_shadow)
    assert manager._epoch.wake_cycle_id == initial + 1
    manager.end_request_cycle()
    manager.begin_request_cycle(config_with_tiers_shadow)
    assert manager._epoch.wake_cycle_id == initial + 2


def test_begin_request_cycle_runs_fast_sleep_for_inactive_t1(
    manager: TierManager,
    config_with_tiers_shadow: dict,
) -> None:
    manager._states[("tool", "cyt_mcp:idle")] = EntityTierState(
        entity_id="cyt_mcp:idle",
        kind="tool",
        stable_tier=Tier.COLD,
        effective_tier=Tier.COLD,
    )
    manager.begin_request_cycle(config_with_tiers_shadow)
    state = manager._states[("tool", "cyt_mcp:idle")]
    assert state.effective_tier == Tier.DORMANT
    manager.end_request_cycle()


def test_epoch_remaining_ms_respects_ttl(
    manager: TierManager,
    config_with_tiers_shadow: dict,
) -> None:
    cfg = tier_section_config(config_with_tiers_shadow, kind="tool")
    now_ms = 1_000_000
    manager._epoch.epoch_start_ms = now_ms - 60_000
    remaining = epoch_remaining_ms(now_ms=now_ms, epoch=manager._epoch, cfg=cfg)
    assert remaining == epoch_ttl_ms(cfg) - 60_000
    assert remaining > 0


def test_status_advances_expired_epoch_for_remaining_display(
    manager: TierManager,
    config_with_tiers_shadow: dict,
) -> None:
    cfg = tier_section_config(config_with_tiers_shadow, kind="tool")
    ttl_ms = epoch_ttl_ms(cfg)
    expired_start = int(time.time() * 1000) - ttl_ms - 60_000
    manager._epoch.epoch_start_ms = expired_start
    manager._epoch.last_request_ms = expired_start
    initial_epoch_id = manager._epoch.epoch_id

    summary = manager.status(config_with_tiers_shadow)

    assert manager._epoch.epoch_id == initial_epoch_id + 1
    assert summary["epoch_remaining_seconds"] > 0
    assert summary["epoch_remaining_seconds"] <= ttl_ms // 1000

"""Unit tests for tier manager and adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.tiers.adapters.tools import apply_tool_tiers, merge_t4_tools, tool_entity_id
from cyt.tiers.config import tier_section_config
from cyt.tiers.evaluator import epoch_boundary, evaluate_slow_clock
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


def test_apply_tool_tiers_excludes_t0_when_enabled(config_with_tiers_enabled: dict) -> None:
    tools = [
        {"name": "a", "cyt_catalog_source": "cyt_mcp", "description": "A"},
        {"name": "b", "cyt_catalog_source": "cyt_mcp", "description": "B"},
    ]
    tier_map = {
        tool_entity_id(tools[0]): Tier.DORMANT,
        tool_entity_id(tools[1]): Tier.ACTIVE,
    }
    result = apply_tool_tiers(tools, tier_for_tool=tier_map, apply=True)
    assert len(result.eligible_tools) == 1
    assert result.eligible_tools[0]["name"] == "b"
    assert tool_entity_id(tools[0]) in result.excluded_t0


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
        "tools": {**base_config.get("tools", {}), "tiers": {"enabled": False, "shadow": True}},
    }
    tools = [{"name": "x", "cyt_catalog_source": "cyt_mcp"}]
    manager.record_tool_candidates(tools, cfg)
    state = manager._states[("tool", "cyt_mcp:x")]
    assert state.stats.candidates >= 1.0


def test_fast_wake_from_dormant(manager: TierManager, base_config: dict) -> None:
    cfg = tier_section_config(
        {**base_config, "tools": {"tiers": {"enabled": False, "shadow": True}}},
        kind="tool",
    )
    state = EntityTierState(
        entity_id="cyt_mcp:x",
        kind="tool",
        stable_tier=Tier.DORMANT,
        effective_tier=Tier.DORMANT,
        stats=EffectiveStats(used_without_injection=1),
    )
    transition = evaluate_fast_wake(state, cfg=cfg, session_id=1)
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


@pytest.fixture
def base_config() -> dict:
    from cyt.config import load_config

    return load_config()


@pytest.fixture
def config_with_tiers_enabled(base_config: dict) -> dict:
    tools = dict(base_config.get("tools") or {})
    tools["tiers"] = {"enabled": True, "shadow": False}
    return {**base_config, "tools": tools}


@pytest.fixture
def config_with_tiers_shadow(base_config: dict) -> dict:
    tools = dict(base_config.get("tools") or {})
    tools["tiers"] = {"enabled": False, "shadow": True}
    return {**base_config, "tools": tools}

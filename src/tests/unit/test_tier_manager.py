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

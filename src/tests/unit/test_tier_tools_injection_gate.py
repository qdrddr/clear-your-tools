"""Tests for gating tool tier prompt eval and stats display on tools.enabled."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.pruners.tools_filter import _tier_prune_context
from cyt.tiers.cli import main as tiers_main
from cyt.tiers.config import tier_section_config, tools_tier_prompt_eval_active
from cyt.tiers.evaluator import evaluate_slow_clock
from cyt.tiers.manager import _managers, get_tier_manager
from cyt.tiers.models import EffectiveStats, EntityTierState, EpochState, Tier
from cyt.tiers.shadow import schedule_tool_shadow_evaluation
from tests.support.tiers_stats_fixtures import patch_load_config


def _tools_enabled_config(db_path: Path) -> dict[str, Any]:
    return {
        "pruning": {"tools": {"enabled": True}},
        "skills": {"enabled": True, "tiers": {"mode": "live"}},
        "tools": {"tiers": {"mode": "live", "database": {"path": str(db_path)}}},
    }


def _tools_disabled_config(db_path: Path) -> dict[str, Any]:
    return {
        "pruning": {
            "tools": {
                "enabled": False,
                "hook": {
                    "tools_from": ["cyt_mcp"],
                    "cyt_mcp": {"agent": "cursor"},
                },
            },
        },
        "skills": {"enabled": True, "tiers": {"mode": "live"}},
        "tools": {"tiers": {"mode": "live", "database": {"path": str(db_path)}}},
    }


def test_tools_tier_prompt_eval_active_requires_injection_enabled() -> None:
    assert tools_tier_prompt_eval_active(
        {"pruning": {"tools": {"enabled": True}}, "tools": {"tiers": {"mode": "live"}}},
    )
    assert not tools_tier_prompt_eval_active(
        {"pruning": {"tools": {"enabled": False}}, "tools": {"tiers": {"mode": "live"}}},
    )
    assert not tools_tier_prompt_eval_active(
        {"pruning": {"tools": {"enabled": True}}, "tools": {"tiers": {"mode": "off"}}},
    )


def test_tier_prune_context_skips_prompt_eval_when_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    apply_tools = MagicMock()
    record_candidates = MagicMock()
    fake_manager = MagicMock()
    fake_manager.apply_tools = apply_tools
    fake_manager.record_tool_candidates = record_candidates
    monkeypatch.setattr(
        "cyt.tiers.manager.get_tier_manager_for_config",
        lambda *_args, **_kwargs: fake_manager,
    )

    original_tools = [{"name": "search", "cyt_catalog_source": "cyt_mcp"}]
    _tier_prune_context(
        original_tools,
        {"pruning": {"tools": {"enabled": False}}, "tools": {"tiers": {"mode": "live"}}},
        ctx=None,
        configured_pipeline=["bm25"],
        for_hook=True,
    )

    apply_tools.assert_not_called()
    record_candidates.assert_not_called()


def test_schedule_tool_shadow_evaluation_skips_when_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    apply_shadow = MagicMock()
    fake_manager = MagicMock()
    fake_manager.apply_shadow_tool_hits = apply_shadow
    monkeypatch.setattr(
        "cyt.tiers.shadow._lexical_shadow_hits",
        lambda *_args, **_kwargs: [("cyt_mcp:search", 0.85)],
    )

    from cyt.tiers.models import ToolsTierApplyResult

    schedule_tool_shadow_evaluation(
        config={"pruning": {"tools": {"enabled": False}}, "tools": {"tiers": {"mode": "live"}}},
        query="search demo",
        original_tools=[{"name": "search", "cyt_catalog_source": "cyt_mcp"}],
        tier_apply=ToolsTierApplyResult(
            eligible_tools=[],
            t4_direct=[],
            excluded_t0=["cyt_mcp:search"],
            policy_overrides={},
            tier_by_tool={},
        ),
        manager=fake_manager,
    )

    apply_shadow.assert_not_called()


def test_tier_state_accumulates_on_tool_used_when_injection_disabled(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config = _tools_disabled_config(db_path)
    _managers.clear()
    manager = get_tier_manager(config, workspace=tmp_path)
    manager.record_tool_used(
        {"name": "search", "cyt_catalog_source": "cyt_mcp"},
        config=config,
    )
    state = manager._states.get(("tool", "cyt_mcp:search"))
    assert state is not None
    assert state.stats.used == 1.0


def test_epoch_still_processes_tool_states_when_injection_disabled() -> None:
    cfg = tier_section_config(
        {
            "tools": {
                "tiers": {
                    "emergency_t4_inject_min": 3,
                    "emergency_t4_utility_max": 0.99,
                },
            },
        },
        kind="tool",
    )
    state = EntityTierState(
        entity_id="cyt_mcp:hot",
        kind="tool",
        stable_tier=Tier.EXTRA_HOT,
        effective_tier=Tier.EXTRA_HOT,
        stats=EffectiveStats(injected=10.0, used=0.0, candidates=20.0),
    )
    transitions = evaluate_slow_clock({("tool", "cyt_mcp:hot"): state}, cfg=cfg, epoch=EpochState())
    assert any(t.kind == "tool" and t.reason == "emergency_t4_eviction" for t in transitions)


def test_tiers_stats_omits_tools_when_injection_disabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config = _tools_disabled_config(db_path)

    patch_load_config(monkeypatch, config)
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: [],
    )
    _managers.clear()
    code = tiers_main(["stats", "--workspace", str(tmp_path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "tools" not in payload
    overview = payload.get("overview") or {}
    tier_stats = overview.get("tier_statistics") or {}
    assert "tools" not in tier_stats
    assert "mcp_servers" not in overview
    assert "db_tool_entities" not in (overview.get("troubleshooting") or {})

    _managers.clear()
    capsys.readouterr()
    code = tiers_main(["stats", "--workspace", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "=== tools ===" not in out
    assert "tools injected=" not in out
    assert "tools used=" not in out


def test_tiers_stats_kind_tools_errors_when_disabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config = _tools_disabled_config(db_path)

    patch_load_config(monkeypatch, config)
    _managers.clear()
    code = tiers_main(["stats", "--workspace", str(tmp_path), "--kind", "tools"])
    assert code == 2
    assert "tools.enabled is false" in capsys.readouterr().err


def test_tiers_stats_server_filter_errors_when_disabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config = _tools_disabled_config(db_path)

    patch_load_config(monkeypatch, config)
    _managers.clear()
    code = tiers_main(["stats", "--workspace", str(tmp_path), "--server", "context-mode"])
    assert code == 2
    assert "tools.enabled is false" in capsys.readouterr().err


def test_build_status_overview_omits_tool_fields_when_disabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from cyt.tiers.status_overview import build_status_overview

    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config = _tools_disabled_config(db_path)

    patch_load_config(monkeypatch, config)
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: [],
    )
    _managers.clear()
    manager = get_tier_manager(config, workspace=tmp_path)
    status = manager.status(config)
    overview = build_status_overview(
        status,
        config=config,
        workspace_root=tmp_path,
        agent="cursor",
        include_tools=False,
    )

    assert "tools" not in overview.get("tiers", {})
    assert "tools" not in overview.get("tier_statistics", {})
    assert "mcp_servers" not in overview
    assert "mcp_config_files" not in overview
    troubleshooting = overview.get("troubleshooting") or {}
    assert "db_tool_entities" not in troubleshooting
    assert "catalog_tool_count" not in troubleshooting


def test_record_tools_hook_injection_writes_stats_db(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from cyt.hook.workspace_config import set_hook_workspace_in_config
    from cyt.proxy.stats import StatsDB
    from cyt.tools.stats import record_tools_hook_injection

    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    stats_path = tmp_path / "stats.db"
    config = set_hook_workspace_in_config(_tools_disabled_config(db_path), tmp_path)
    config["stats"] = {"database": {"path": str(stats_path)}}
    monkeypatch.setattr("cyt.config.stats_db_path", lambda cfg=None: str(stats_path))

    request_id = record_tools_hook_injection(
        query="demo",
        model_name="hook",
        tools_in=42,
        tools_out=3,
        prompt_tokens=100,
        config=config,
    )
    assert request_id is not None

    stats_db = StatsDB.open(str(stats_path))
    try:
        events = stats_db.query_events(limit=5)
        assert any(event.get("endpoint") == "tools-hook" for event in events)
    finally:
        stats_db.close()

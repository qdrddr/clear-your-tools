"""Tests for gating skill tier prompt eval and stats display on skills.enabled."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.tiers.adapters.skills import resolve_tiered_skill_matches
from cyt.tiers.cli import main as tiers_main
from cyt.tiers.config import skills_tier_prompt_eval_active, tier_section_config
from cyt.tiers.evaluator import evaluate_slow_clock
from cyt.tiers.manager import _managers, get_tier_manager
from cyt.tiers.models import EffectiveStats, EntityTierState, EpochState, Tier


def _skills_enabled_config(db_path: Path) -> dict[str, Any]:
    return {
        "skills": {"enabled": True, "tiers": {"mode": "live"}},
        "tools": {"tiers": {"mode": "live", "database": {"path": str(db_path)}}},
    }


def _skills_disabled_config(db_path: Path) -> dict[str, Any]:
    return {
        "skills": {"enabled": False, "tiers": {"mode": "live"}},
        "tools": {"tiers": {"mode": "live", "database": {"path": str(db_path)}}},
    }


def test_skills_tier_prompt_eval_active_requires_injection_enabled() -> None:
    assert skills_tier_prompt_eval_active({"skills": {"enabled": True, "tiers": {"mode": "live"}}})
    assert not skills_tier_prompt_eval_active(
        {"skills": {"enabled": False, "tiers": {"mode": "live"}}},
    )
    assert not skills_tier_prompt_eval_active(
        {"skills": {"enabled": True, "tiers": {"mode": "off"}}},
    )


def test_resolve_tiered_skill_matches_skips_prompt_eval_when_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    partition = MagicMock()
    record = MagicMock()
    fake_manager = MagicMock()
    fake_manager.partition_skills = partition
    fake_manager.record_skill_candidates = record
    monkeypatch.setattr(
        "cyt.tiers.manager.get_tier_manager",
        lambda *_args, **_kwargs: fake_manager,
    )
    search = MagicMock(return_value=[])
    monkeypatch.setattr("cyt.skills.search.search_skills", search)

    resolve_tiered_skill_matches(
        "demo query",
        [],
        config={"skills": {"enabled": False, "tiers": {"mode": "live"}}},
        skip_frontmatter_gate=True,
    )

    partition.assert_not_called()
    record.assert_not_called()
    search.assert_called_once()


def test_tier_state_accumulates_on_skill_used_when_injection_disabled(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config = _skills_disabled_config(db_path)
    _managers.clear()
    manager = get_tier_manager(config, workspace=tmp_path)
    manager.record_skill_used("skill:demo", config=config)
    state = manager._states.get(("skill", "skill:demo"))
    assert state is not None
    assert state.stats.used == 1.0


def test_epoch_still_processes_skill_states_when_injection_disabled() -> None:
    cfg = tier_section_config(
        {
            "skills": {
                "tiers": {
                    "emergency_t4_inject_min": 3,
                    "emergency_t4_utility_max": 0.99,
                },
            },
        },
        kind="skill",
    )
    state = EntityTierState(
        entity_id="skill:hot",
        kind="skill",
        stable_tier=Tier.EXTRA_HOT,
        effective_tier=Tier.EXTRA_HOT,
        stats=EffectiveStats(injected=10.0, used=0.0, candidates=20.0),
    )
    transitions = evaluate_slow_clock({("skill", "skill:hot"): state}, cfg=cfg, epoch=EpochState())
    assert any(t.kind == "skill" and t.reason == "emergency_t4_eviction" for t in transitions)


def test_tiers_stats_omits_skills_when_injection_disabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config = _skills_disabled_config(db_path)
    def _load_config(*args, **kwargs):
        return config

    monkeypatch.setattr("cyt.config.load_config", _load_config)
    monkeypatch.setattr("cyt.tiers.cli.load_config", _load_config)
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: [],
    )
    _managers.clear()
    code = tiers_main(["stats", "--workspace", str(tmp_path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "skills" not in payload
    overview = payload.get("overview") or {}
    tier_stats = overview.get("tier_statistics") or {}
    assert "skills" not in tier_stats

    _managers.clear()
    capsys.readouterr()
    code = tiers_main(["stats", "--workspace", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "=== skills ===" not in out
    assert "skills injected=" not in out
    assert "skills used=" not in out


def test_tiers_stats_kind_skills_errors_when_disabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config = _skills_disabled_config(db_path)
    def _load_config(*args, **kwargs):
        return config

    monkeypatch.setattr("cyt.config.load_config", _load_config)
    monkeypatch.setattr("cyt.tiers.cli.load_config", _load_config)
    _managers.clear()
    code = tiers_main(["stats", "--workspace", str(tmp_path), "--kind", "skills"])
    assert code == 2
    assert "skills.enabled is false" in capsys.readouterr().err


def test_build_status_overview_omits_skill_fields_when_disabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from cyt.tiers.status_overview import build_status_overview

    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config = _skills_disabled_config(db_path)

    def _load_config(*args, **kwargs):
        return config

    monkeypatch.setattr("cyt.config.load_config", _load_config)
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
        include_skills=False,
    )

    assert "skills" not in overview.get("tiers", {})
    assert "skills" not in overview.get("tier_statistics", {})
    assert "skill_directories" not in overview
    troubleshooting = overview.get("troubleshooting") or {}
    assert "db_skill_entities" not in troubleshooting


def test_record_skills_injection_writes_both_dbs(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from cyt.hook.workspace_config import set_hook_workspace_in_config
    from cyt.proxy.stats import StatsDB
    from cyt.skills.stats import record_skills_injection
    from cyt.tiers.manager import TierManager

    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    stats_path = tmp_path / "stats.db"
    config = set_hook_workspace_in_config(_skills_disabled_config(db_path), tmp_path)
    config["stats"] = {"database": {"path": str(stats_path)}}
    tools = dict(config.get("tools") or {})
    tools["tiers"] = {
        **dict(tools.get("tiers") or {}),
        "database": {"path": str(db_path), "disk_flush_seconds": 0},
    }
    config["tools"] = tools
    monkeypatch.setattr("cyt.config.stats_db_path", lambda cfg=None: str(stats_path))

    injected_md = '<agent-skills><skill name="demo" path="skill/demo">body</skill></agent-skills>'
    request_id = record_skills_injection(
        query="demo",
        model_name="hook",
        skills_in=42,
        skills_final_md=injected_md,
        config=config,
    )
    assert request_id is not None

    stats_db = StatsDB.open(str(stats_path))
    try:
        tokens = stats_db.query_skills_injection_tokens("all")
        assert tokens
    finally:
        stats_db.close()

    _managers.clear()
    manager = TierManager(tmp_path, str(db_path))
    try:
        state = manager._states.get(("skill", "skill/demo"))
        assert state is not None
        assert state.stats.injected == 1.0
    finally:
        manager.close()

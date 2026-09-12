"""Tests for tier-based permissions proposals."""

from __future__ import annotations

import time
from pathlib import Path

from cyt.permissions.proposals import (
    SkillProposal,
    TierProposalBundle,
    ToolProposal,
    WizardConfig,
    _entity_eligible,
    wizard_config_from_dict,
)


def test_entity_eligible_t0_accepts_zero_injection_stats() -> None:
    wizard_cfg = WizardConfig(min_candidates=10, idle_ms=60_000)
    now_ms = int(time.time() * 1000)
    entity = {
        "base_tier": "T0",
        "hints": [],
        "stats": {
            "used": 0,
            "candidates": 0,
            "injected": 0,
            "last_seen_ms": 0,
        },
    }
    assert _entity_eligible(
        entity,
        target_tier="T0",
        wizard_cfg=wizard_cfg,
        min_injections=8,
        now_ms=now_ms,
    )


def test_entity_eligible_t1_still_requires_injection_stats() -> None:
    wizard_cfg = WizardConfig(min_candidates=10, idle_ms=60_000)
    now_ms = int(time.time() * 1000)
    entity = {
        "base_tier": "T1",
        "hints": [],
        "stats": {
            "used": 0,
            "candidates": 0,
            "injected": 0,
            "last_seen_ms": 0,
        },
    }
    assert not _entity_eligible(
        entity,
        target_tier="T1",
        wizard_cfg=wizard_cfg,
        min_injections=8,
        now_ms=now_ms,
    )


def test_entity_eligible_requires_usage_and_idle_stats() -> None:
    wizard_cfg = WizardConfig(min_candidates=5, idle_ms=60_000)
    now_ms = int(time.time() * 1000)
    entity = {
        "base_tier": "T0",
        "hints": [],
        "stats": {
            "used": 0,
            "candidates": 10,
            "injected": 0,
            "last_seen_ms": now_ms - 120_000,
        },
    }
    assert _entity_eligible(
        entity,
        target_tier="T0",
        wizard_cfg=wizard_cfg,
        min_injections=8,
        now_ms=now_ms,
    )

    entity_stats = entity.get("stats")
    stats_dict = entity_stats if isinstance(entity_stats, dict) else {}
    entity_recent = {
        **entity,
        "stats": {**stats_dict, "last_seen_ms": now_ms - 1_000},
    }
    assert not _entity_eligible(
        entity_recent,
        target_tier="T0",
        wizard_cfg=wizard_cfg,
        min_injections=8,
        now_ms=now_ms,
    )


def test_entity_eligible_excludes_wake_candidate() -> None:
    wizard_cfg = WizardConfig(min_candidates=1, idle_ms=0)
    now_ms = int(time.time() * 1000)
    entity = {
        "base_tier": "T0",
        "hints": ["wake_candidate"],
        "stats": {"used": 0, "candidates": 10, "injected": 0, "last_seen_ms": 0},
    }
    assert not _entity_eligible(
        entity,
        target_tier="T0",
        wizard_cfg=wizard_cfg,
        min_injections=1,
        now_ms=now_ms,
    )


def test_wizard_config_from_dict_uses_defaults() -> None:
    cfg = wizard_config_from_dict({})
    assert cfg.min_candidates >= 8
    assert cfg.idle_ms >= 0


def test_bundle_summary_counts() -> None:
    bundle = TierProposalBundle(
        tier="T0",
        tools=[ToolProposal(server="srv", tool="alpha", entity_id="x", tier="T0")],
        skills=[
            SkillProposal(name="a", path=Path("a/SKILL.md"), entity_id="y", tier="T0"),
            SkillProposal(name="b", path=Path("b/SKILL.md"), entity_id="z", tier="T0"),
        ],
    )
    from cyt.permissions.proposals import bundle_summary

    summary = bundle_summary(bundle)
    assert summary["tools"] == 1
    assert summary["skills"] == 2


def test_format_tool_proposal_display_includes_server_config_line() -> None:
    from cyt.permissions.proposals import format_tool_proposal_display

    home = Path.home()
    proposal = ToolProposal(
        server="demo",
        tool="search",
        entity_id="x",
        tier="T0",
        server_config_path=str(home / ".config" / "cyt" / "mcp" / "cursor.json"),
        server_config_line=7,
    )
    assert (
        format_tool_proposal_display(proposal)
        == "tool demo/search  (~/.config/cyt/mcp/cursor.json:L7)"
    )


def test_format_skill_proposal_display_includes_config_directory(tmp_path: Path) -> None:
    from cyt.permissions.proposals import SkillProposal, format_skill_proposal_display

    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / ".agents" / "cyt" / "config" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    proposal = SkillProposal(
        name="demo",
        path=repo / "demo" / "SKILL.md",
        entity_id="x",
        tier="T1",
        discovery_config_path=str(config_path),
        discovery_directory=".agents/skills",
    )
    assert (
        format_skill_proposal_display(proposal, workspace_root=repo)
        == "skill demo  (.agents/cyt/config/config.yaml: .agents/skills)"
    )


def test_format_wizard_path_shortens_home_and_workspace() -> None:
    from cyt.permissions.proposals import _format_wizard_path

    home = Path.home()
    assert _format_wizard_path(home / ".cursor" / "skills-cursor") == "~/.cursor/skills-cursor"


def test_wizard_tool_tier_skip_notes_reports_denied_tools() -> None:
    from cyt.permissions.proposals import WizardConfig, _wizard_tool_tier_skip_notes

    wizard_cfg = WizardConfig(min_candidates=10, idle_ms=60_000)
    now_ms = int(time.time() * 1000)
    tool_items = [
        {
            "entity_id": "cyt_mcp:jcodemunch_search_symbols",
            "name": "jcodemunch_search_symbols",
            "base_tier": "T0",
            "stats": {"used": 0, "candidates": 0, "injected": 0, "last_seen_ms": 0},
        },
        {
            "entity_id": "cyt_mcp:context-mode_ctx_search",
            "name": "context-mode_ctx_search",
            "base_tier": "T0",
            "stats": {"used": 0, "candidates": 0, "injected": 0, "last_seen_ms": 0},
        },
    ]
    skipped_denied, skipped_recent = _wizard_tool_tier_skip_notes(
        tool_items,
        target_tier="T0",
        wizard_cfg=wizard_cfg,
        min_injections=8,
        now_ms=now_ms,
        effective_deny=("jcodemunch/search_symbols",),
    )
    assert skipped_denied == ["jcodemunch_search_symbols"]
    assert skipped_recent == []

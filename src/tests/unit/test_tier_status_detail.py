"""Unit tests for tier status detail serialization."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from cyt.tiers.adapters.skills import stable_skill_doc_entity_id
from cyt.tiers.config import TierSectionConfig, tier_section_config
from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState, Tier
from cyt.tiers.status_detail import (
    build_kind_detail,
    effective_tier_for,
    enrich_skill_detail_with_workspace_discoveries,
    enrich_tool_detail_with_catalog_discoveries,
    entity_status_dict,
    filter_skill_detail_by_agent,
    filter_skill_detail_by_permissions,
    filter_tool_detail_by_permissions,
)
from tests.support.skills_helpers import isolated_skills_agents_block


def _cfg() -> TierSectionConfig:
    return tier_section_config({"tools": {"tiers": {}}}, kind="tool")


def test_effective_tier_for_active_temp_promotion() -> None:
    now_ms = int(time.time() * 1000)
    state = EntityTierState(
        entity_id="cyt_mcp:search",
        kind="tool",
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.HOT,
        temp_promotion_until_ms=now_ms + 60_000,
    )
    assert effective_tier_for(state, now_ms=now_ms) == Tier.HOT


def test_effective_tier_for_expired_temp_promotion() -> None:
    now_ms = int(time.time() * 1000)
    state = EntityTierState(
        entity_id="cyt_mcp:search",
        kind="tool",
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.HOT,
        temp_promotion_until_ms=now_ms - 1,
    )
    assert effective_tier_for(state, now_ms=now_ms) == Tier.HOT


def test_effective_tier_for_overlap_tier() -> None:
    state = EntityTierState(
        entity_id="cyt_mcp:search",
        kind="tool",
        stable_tier=Tier.COLD,
        effective_tier=Tier.ACTIVE,
        overlap_tier=Tier.COLD,
    )
    assert effective_tier_for(state) == Tier.ACTIVE


def test_entity_status_dict_temporary_tier_when_effective_above_base() -> None:
    now_ms = int(time.time() * 1000)
    state = EntityTierState(
        entity_id="cyt_mcp:search",
        kind="tool",
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.HOT,
        overlap_tier=Tier.ACTIVE,
        temp_promotion_until_ms=now_ms + 120_000,
        stats=EffectiveStats(candidates=100, injected=50, used=20),
    )
    detail = entity_status_dict(state, cfg=_cfg(), wake_cycle_id=3, now_ms=now_ms)
    assert detail["base_tier"] == "T2"
    assert detail["effective_tier"] == "T3"
    assert detail["temporary_tier"] == "T3"
    assert detail["temporary"] is True
    assert detail["temp_promotion_expires_in_sec"] == 120
    assert detail["policy"] == "tier_hot"
    assert detail["scores"]["demand"] > 0
    assert "temp_promotion_active" in detail["hints"]


def test_entity_status_dict_attaches_skill_token_count(tmp_path: Path) -> None:
    from cyt.tiers.skill_token_materialization import clear_carried_token_memo

    clear_carried_token_memo()
    skill_file = tmp_path / ".cursor" / "skills" / "demo-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("---\nname: demo\n---\nbody text here\n", encoding="utf-8")
    state = EntityTierState(
        entity_id=str(skill_file),
        kind="skill",
        stable_tier=Tier.COLD,
        effective_tier=Tier.COLD,
    )
    detail = entity_status_dict(
        state,
        cfg=tier_section_config({"skills": {"tiers": {}}}, kind="skill"),
        wake_cycle_id=1,
        kind=EntityKind.SKILL,
        config={"skills": {"tiers": {}}},
        workspace_root=tmp_path,
    )
    assert detail.get("source_path") == str(skill_file)
    assert isinstance(detail.get("token_count"), int)
    assert detail["token_count"] > 0


def test_entity_status_dict_attaches_tool_token_count() -> None:
    from cyt.tiers.tool_token_materialization import clear_carried_token_memo

    clear_carried_token_memo()
    catalog = [
        {
            "name": "search",
            "cyt_catalog_source": "cyt_mcp",
            "description": "Search tool",
            "token_count": 512,
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ]
    state = EntityTierState(
        entity_id="cyt_mcp:search",
        kind="tool",
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.ACTIVE,
    )
    detail = entity_status_dict(
        state,
        cfg=_cfg(),
        wake_cycle_id=1,
        kind=EntityKind.TOOL,
        catalog_tools=catalog,
    )
    assert detail.get("token_count") == 512


def test_entity_status_dict_no_temporary_tier_when_stable() -> None:
    state = EntityTierState(
        entity_id="cyt_mcp:read",
        kind="tool",
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.ACTIVE,
        stats=EffectiveStats(candidates=10, injected=2, used=0),
    )
    detail = entity_status_dict(state, cfg=_cfg(), wake_cycle_id=1)
    assert detail["temporary_tier"] is None
    assert detail["temporary"] is False
    assert "below_min_injections" in detail["hints"]


def test_skill_status_fields_include_frontmatter_name(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_path = skills_dir / "create-hook.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: create-hook\ndescription: Agent hooks for sessions.\n---\n# Create Hook\n",
        encoding="utf-8",
    )
    state = EntityTierState(
        entity_id=stable_skill_doc_entity_id("create-hook"),
        kind="skill",
        stable_tier=Tier.ACTIVE,
        effective_tier=Tier.ACTIVE,
    )
    config = {"skills": {"directories": [str(skills_dir)]}}
    detail = entity_status_dict(
        state,
        cfg=_cfg(),
        wake_cycle_id=1,
        kind=EntityKind.SKILL,
        config=config,
    )
    assert detail["name"] == "create-hook"
    assert detail["source_path"] == str(skill_path.resolve())


def test_enrich_tool_detail_adds_loaded_catalog_tools() -> None:
    cfg = _cfg()
    catalog = [
        {"name": "alpha_tool", "cyt_catalog_source": "cyt_mcp"},
        {"name": "beta_tool", "cyt_catalog_source": "cyt_mcp"},
    ]
    states = {
        ("tool", "cyt_mcp:alpha_tool"): EntityTierState(
            entity_id="cyt_mcp:alpha_tool",
            kind="tool",
            stable_tier=Tier.HOT,
            effective_tier=Tier.HOT,
            stats=EffectiveStats(injected=1),
        ),
    }
    detail = build_kind_detail(
        states,
        kind="tool",
        cfg=cfg,
        wake_cycle_id=1,
        config={"tools": {"hook": {"tools_from": ["cyt_mcp"]}}},
        tracked_catalog_entity_ids=frozenset(
            {"cyt_mcp:alpha_tool", "cyt_mcp:beta_tool"},
        ),
    )
    enriched = enrich_tool_detail_with_catalog_discoveries(
        detail,
        states=states,
        cfg=cfg,
        config={"tools": {"hook": {"tools_from": ["cyt_mcp"]}}},
        workspace_root=None,
        catalog_tools=catalog,
        wake_cycle_id=1,
    )
    entity_ids = {row["entity_id"] for items in enriched["by_tier"].values() for row in items}
    assert entity_ids == {"cyt_mcp:alpha_tool", "cyt_mcp:beta_tool"}
    assert enriched["histogram"]["T3"] == 1
    assert enriched["histogram"]["T2"] == 1
    assert enriched["by_tier"]["T2"][0]["entity_id"] == "cyt_mcp:beta_tool"


def test_build_kind_detail_hides_catalog_orphans_and_candidate_only_tools() -> None:
    cfg = _cfg()
    states = {
        ("tool", "cyt_mcp:live_tool"): EntityTierState(
            entity_id="cyt_mcp:live_tool",
            kind="tool",
            stable_tier=Tier.ACTIVE,
            effective_tier=Tier.ACTIVE,
            stats=EffectiveStats(injected=2, used=1),
        ),
        ("tool", "cyt_mcp:mcp__x__tool"): EntityTierState(
            entity_id="cyt_mcp:mcp__x__tool",
            kind="tool",
            stable_tier=Tier.ACTIVE,
            effective_tier=Tier.ACTIVE,
            stats=EffectiveStats(injected=5, used=2),
        ),
        ("tool", "cyt_mcp:candidate_only"): EntityTierState(
            entity_id="cyt_mcp:candidate_only",
            kind="tool",
            stable_tier=Tier.ACTIVE,
            effective_tier=Tier.ACTIVE,
            stats=EffectiveStats(candidates=3),
        ),
    }
    detail = build_kind_detail(
        states,
        kind="tool",
        cfg=cfg,
        wake_cycle_id=1,
        config={"tools": {"hook": {"tools_from": ["cyt_mcp"]}}},
        tracked_catalog_entity_ids=frozenset({"cyt_mcp:live_tool", "cyt_mcp:candidate_only"}),
        require_tool_engagement=True,
    )
    entity_ids = {row["entity_id"] for row in detail["by_tier"]["T2"]}
    assert entity_ids == {"cyt_mcp:live_tool"}


def test_build_kind_detail_groups_and_histogram_match() -> None:
    cfg = _cfg()
    states = {
        ("tool", "cyt_mcp:a"): EntityTierState(
            entity_id="cyt_mcp:a",
            kind="tool",
            stable_tier=Tier.DORMANT,
            effective_tier=Tier.DORMANT,
        ),
        ("tool", "cyt_mcp:b"): EntityTierState(
            entity_id="cyt_mcp:b",
            kind="tool",
            stable_tier=Tier.ACTIVE,
            effective_tier=Tier.ACTIVE,
            stats=EffectiveStats(candidates=5, injected=5, used=2),
        ),
        ("skill", "skill:x"): EntityTierState(
            entity_id="skill:x",
            kind="skill",
            stable_tier=Tier.HOT,
            effective_tier=Tier.HOT,
        ),
    }
    detail = build_kind_detail(states, kind="tool", cfg=cfg, wake_cycle_id=1)
    assert detail["histogram"]["T0"] == 1
    assert detail["histogram"]["T2"] == 1
    assert len(detail["by_tier"]["T0"]) == 1
    assert len(detail["by_tier"]["T2"]) == 1
    assert detail["by_tier"]["T0"][0]["entity_id"] == "cyt_mcp:a"
    assert detail["by_tier"]["T2"][0]["entity_id"] == "cyt_mcp:b"


def test_enrich_skill_detail_adds_workspace_discovered_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    skill_dir = workspace / ".agents" / "skills" / "explain-simply"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\nname: explain-simply\ndescription: Explain simply.\n---\n# Explain\n",
        encoding="utf-8",
    )

    config = {
        "skills": {"directories": [".agents/skills"]},
        "agents": isolated_skills_agents_block(),
    }
    detail = build_kind_detail({}, kind=EntityKind.SKILL, cfg=_cfg(), wake_cycle_id=1)
    enriched = enrich_skill_detail_with_workspace_discoveries(
        detail,
        states={},
        cfg=_cfg(),
        config=config,
        workspace_root=workspace,
        agent="cursor",
        wake_cycle_id=1,
    )

    assert enriched["histogram"]["T0"] == 1
    dormant = enriched["by_tier"]["T0"]
    assert len(dormant) == 1
    assert dormant[0]["name"] == "explain-simply"
    assert dormant[0]["display_name"] == "explain-simply"
    assert dormant[0]["source_path"] == str(skill_path.resolve())


def test_enrich_skill_detail_skips_tracked_and_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    tracked_dir = workspace / ".agents" / "skills" / "tracked-skill"
    tracked_dir.mkdir(parents=True)
    (tracked_dir / "SKILL.md").write_text(
        "---\nname: tracked-skill\ndescription: tracked\n---\n# Tracked\n",
        encoding="utf-8",
    )

    outside = tmp_path / "outside-skills"
    outside.mkdir()
    (outside / "outside.md").write_text(
        "---\nname: outside-skill\ndescription: outside\n---\n# Outside\n",
        encoding="utf-8",
    )

    tracked_path = str((tracked_dir / "SKILL.md").resolve())
    states = {
        ("skill", tracked_path): EntityTierState(
            entity_id=tracked_path,
            kind="skill",
            stable_tier=Tier.ACTIVE,
            effective_tier=Tier.ACTIVE,
        ),
    }
    config = {
        "skills": {"directories": [".agents/skills", str(outside)]},
        "agents": isolated_skills_agents_block(),
    }
    detail = build_kind_detail(states, kind=EntityKind.SKILL, cfg=_cfg(), wake_cycle_id=1)
    enriched = enrich_skill_detail_with_workspace_discoveries(
        detail,
        states=states,
        cfg=_cfg(),
        config=config,
        workspace_root=workspace,
        agent="cursor",
        wake_cycle_id=1,
    )

    assert enriched["histogram"]["T2"] == 1
    assert enriched["histogram"]["T0"] == 0
    labels = {
        item.get("name") for tier_items in enriched["by_tier"].values() for item in tier_items
    }
    assert "tracked-skill" in labels
    assert "outside-skill" not in labels


def test_filter_skill_detail_by_agent_excludes_other_agent_workspace_skills(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    cursor_dir = workspace / ".agents" / "skills" / "explain-simply"
    cursor_dir.mkdir(parents=True)
    cursor_skill = cursor_dir / "SKILL.md"
    cursor_skill.write_text(
        "---\nname: explain-simply\ndescription: shared\n---\n# Explain\n",
        encoding="utf-8",
    )
    claude_dir = workspace / ".claude" / "skills" / "gitnexus-cli"
    claude_dir.mkdir(parents=True)
    claude_skill = claude_dir / "SKILL.md"
    claude_skill.write_text(
        "---\nname: gitnexus-cli\ndescription: claude only\n---\n# GitNexus\n",
        encoding="utf-8",
    )

    config = {
        "skills": {"directories": [".agents/skills"]},
        "agents": isolated_skills_agents_block(),
    }
    detail = {
        "histogram": {"T0": 2, "T1": 0, "T2": 0, "T3": 0, "T4": 0},
        "by_tier": {
            "T0": [
                {
                    "entity_id": str(cursor_skill.resolve()),
                    "name": "explain-simply",
                    "source_path": str(cursor_skill.resolve()),
                },
                {
                    "entity_id": str(claude_skill.resolve()),
                    "name": "gitnexus-cli",
                    "source_path": str(claude_skill.resolve()),
                },
            ],
            "T1": [],
            "T2": [],
            "T3": [],
            "T4": [],
        },
    }

    filtered = filter_skill_detail_by_agent(
        detail,
        agent="cursor",
        config=config,
        workspace_root=workspace,
    )
    names = {item.get("name") for tier_items in filtered["by_tier"].values() for item in tier_items}
    assert names == {"explain-simply"}
    assert filtered["histogram"]["T0"] == 1


def test_filter_skill_detail_by_permissions_excludes_denied_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    allowed_dir = workspace / ".agents" / "skills" / "explain-simply"
    allowed_dir.mkdir(parents=True)
    allowed_skill = allowed_dir / "SKILL.md"
    allowed_skill.write_text(
        "---\nname: explain-simply\ndescription: allowed\n---\n",
        encoding="utf-8",
    )
    denied_dir = workspace / ".agents" / "skills" / "create-hook"
    denied_dir.mkdir(parents=True)
    denied_skill = denied_dir / "SKILL.md"
    denied_skill.write_text(
        "---\nname: create-hook\ndescription: denied\n---\n",
        encoding="utf-8",
    )

    detail = {
        "histogram": {"T0": 1, "T1": 1, "T2": 0, "T3": 0, "T4": 0},
        "by_tier": {
            "T0": [
                {
                    "entity_id": str(allowed_skill.resolve()),
                    "name": "explain-simply",
                    "source_path": str(allowed_skill.resolve()),
                },
            ],
            "T1": [
                {
                    "entity_id": str(denied_skill.resolve()),
                    "name": "create-hook",
                    "source_path": str(denied_skill.resolve()),
                },
            ],
            "T2": [],
            "T3": [],
            "T4": [],
        },
    }

    from cyt.permissions.schema import EffectivePermissions, SkillsPermissions

    def fake_effective(
        *,
        agent: str,
        workspace_root: Path | None = None,
        **kwargs: object,
    ) -> EffectivePermissions:
        return EffectivePermissions(
            skills=SkillsPermissions(
                deny=(f"path:{denied_dir}/",),
            ),
        )

    monkeypatch.setattr(
        "cyt.permissions.merge.effective_permissions",
        fake_effective,
    )

    filtered = filter_skill_detail_by_permissions(
        detail,
        agent="cursor",
        workspace_root=workspace,
    )
    names = {item.get("name") for tier_items in filtered["by_tier"].values() for item in tier_items}
    assert names == {"explain-simply"}
    assert filtered["histogram"]["T0"] == 1
    assert filtered["histogram"]["T1"] == 0


def test_filter_tool_detail_by_permissions_excludes_denied_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = {
        "histogram": {"T0": 1, "T1": 1, "T2": 0, "T3": 0, "T4": 0},
        "by_tier": {
            "T0": [
                {
                    "entity_id": "cyt_mcp:jcodemunch_search_symbols",
                    "name": "jcodemunch_search_symbols",
                },
            ],
            "T1": [
                {
                    "entity_id": "cyt_mcp:context-mode_ctx_search",
                    "name": "context-mode_ctx_search",
                },
            ],
            "T2": [],
            "T3": [],
            "T4": [],
        },
    }

    from cyt.permissions.schema import EffectivePermissions, McpPermissions

    def fake_effective(
        *,
        agent: str,
        workspace_root: Path | None = None,
        **kwargs: object,
    ) -> EffectivePermissions:
        return EffectivePermissions(
            mcp=McpPermissions(
                deny=("jcodemunch/search_symbols",),
            ),
        )

    monkeypatch.setattr(
        "cyt.permissions.merge.effective_permissions",
        fake_effective,
    )

    filtered = filter_tool_detail_by_permissions(
        detail,
        agent="cursor",
        workspace_root=tmp_path,
    )
    names = {item.get("name") for tier_items in filtered["by_tier"].values() for item in tier_items}
    assert names == {"context-mode_ctx_search"}
    assert filtered["histogram"]["T0"] == 0
    assert filtered["histogram"]["T1"] == 1

"""Unit tests for tier-aware skill injection formatting."""

from __future__ import annotations

from cyt.injection.tier_legend import SKILL_TIER_LEGEND
from cyt.skills.inject import (
    format_agent_skills,
    format_agent_skills_empty,
    format_skill_item,
    format_skills_grouped_by_tier,
)
from cyt.skills.search import MatchedSkill

_SKILL_MARKDOWN = (
    "---\nname: demo-skill\ndescription: Demo skill.\n---\n\n# Demo\n\nBody content.\n"
)


def _skill_match(*, tier: str | None = "t3") -> MatchedSkill:
    return MatchedSkill(
        doc_id="demo-skill",
        file_path="/home/user/skills/demo-skill.md",
        markdown=_SKILL_MARKDOWN,
        name="demo-skill",
        score=1.0,
        token_count=10,
        injection_tier=tier,
    )


def test_format_skill_item_omits_tier_attribute() -> None:
    item = format_skill_item(_skill_match(tier="t2"))
    assert 'tier="' not in item
    assert "tier='" not in item


def test_format_skills_grouped_by_tier_wraps_skills() -> None:
    t3 = _skill_match(tier="t3")
    t2 = MatchedSkill(
        doc_id="other-skill",
        file_path="/home/user/skills/other-skill.md",
        markdown=_SKILL_MARKDOWN,
        name="other-skill",
        score=1.0,
        token_count=10,
        injection_tier="t2",
    )
    block = format_skills_grouped_by_tier([t3, t2])
    assert "<tier_t3>" in block
    assert "</tier_t3>" in block
    assert "<tier_t2>" in block
    assert "</tier_t2>" in block
    assert block.index("<tier_t3>") < block.index("<tier_t2>")
    for line in block.splitlines():
        if line.startswith("<skill"):
            assert 'tier="' not in line


def test_format_agent_skills_includes_tier_legend_and_wrappers() -> None:
    block = format_agent_skills([_skill_match()])
    assert SKILL_TIER_LEGEND in block
    assert "<agent-skills>" in block
    assert "<tier_t3>" in block
    assert 'tier="' not in block


def test_format_agent_skills_omits_tier_legend_when_pre_exposed() -> None:
    prior = format_agent_skills([_skill_match()])
    other = MatchedSkill(
        doc_id="other-skill",
        file_path="/home/user/skills/other-skill.md",
        markdown=_SKILL_MARKDOWN,
        name="other-skill",
        score=1.0,
        token_count=10,
        injection_tier="t3",
    )
    block = format_agent_skills([other], combined_text=prior)
    assert SKILL_TIER_LEGEND not in block


def test_format_agent_skills_empty_emits_stable_wrapper() -> None:
    block = format_agent_skills_empty()
    assert SKILL_TIER_LEGEND in block
    assert "<agent-skills>" in block
    assert "</agent-skills>" in block


def test_format_agent_skills_empty_omits_wrapper_when_legend_pre_exposed() -> None:
    prior = format_agent_skills_empty()
    block = format_agent_skills_empty(combined_text=prior)
    assert block == ""

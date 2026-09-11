"""Tests for tier-scoped skill token materialization."""

from __future__ import annotations

from pathlib import Path

from cyt.tiers.skill_token_materialization import (
    carried_skill_token_count,
    clear_carried_token_memo,
    description_and_headers_markdown,
    effective_skill_token_count,
    markdown_for_tier,
)


def _sample_skill_markdown() -> str:
    return """---
name: demo-skill
description: A demo skill for token stats.
---
# Overview

Intro paragraph with extra body text that should not appear at T2.

## When To Use

Use this skill when testing tier stats.

### Details

More body content here.
"""


def _entity_for_markdown(markdown: str, *, source_path: str = "/tmp/demo/SKILL.md") -> dict:
    return {
        "entity_id": "skill:demo-skill",
        "source_path": source_path,
        "name": "demo-skill",
    }


def test_markdown_for_tier_trims_by_tier() -> None:
    markdown = _sample_skill_markdown()
    entity = _entity_for_markdown(markdown)
    t1 = markdown_for_tier(markdown, "T1", entity=entity)
    t2 = markdown_for_tier(markdown, "T2", entity=entity)
    assert t1.startswith("---")
    assert "description:" in t1
    assert "# Overview" in t2
    assert "Intro paragraph" not in t2
    assert markdown_for_tier(markdown, "T4", entity=entity) == markdown


def test_description_and_headers_includes_description_and_headers() -> None:
    markdown = _sample_skill_markdown()
    combined = description_and_headers_markdown(markdown)
    assert "description:" in combined
    assert "# Overview" in combined
    assert "## When To Use" in combined
    assert "Intro paragraph" not in combined


def test_skill_token_counts_order_by_tier(tmp_path: Path) -> None:
    clear_carried_token_memo()
    skill_file = tmp_path / "demo-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(_sample_skill_markdown(), encoding="utf-8")
    entity = _entity_for_markdown(_sample_skill_markdown(), source_path=str(skill_file))

    carried = carried_skill_token_count(entity)
    assert carried is not None and carried > 0
    assert effective_skill_token_count(entity, "T0") == 0
    eff_t1 = effective_skill_token_count(entity, "T1")
    eff_t2 = effective_skill_token_count(entity, "T2")
    eff_t3 = effective_skill_token_count(entity, "T3")
    eff_t4 = effective_skill_token_count(entity, "T4")
    assert eff_t1 is not None and eff_t2 is not None
    assert eff_t1 <= eff_t2 <= (eff_t3 or 0)
    assert eff_t3 == eff_t4 == carried


def test_carried_equals_effective_at_t4(tmp_path: Path) -> None:
    clear_carried_token_memo()
    skill_file = tmp_path / "demo-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(_sample_skill_markdown(), encoding="utf-8")
    entity = _entity_for_markdown(_sample_skill_markdown(), source_path=str(skill_file))
    assert carried_skill_token_count(entity) == effective_skill_token_count(entity, "T4")

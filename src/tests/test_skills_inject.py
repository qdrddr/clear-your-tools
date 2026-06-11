"""Tests for <agent-skills> injection formatting."""

from __future__ import annotations

from cyt.skills.frontmatter import (
    frontmatter_search_text,
    injection_markdown_body,
    skill_name_from_frontmatter,
)
from cyt.skills.inject import format_agent_skills
from cyt.skills.search import MatchedSkill

_FULL_MARKDOWN = (
    "---\nname: create-hook\ndescription: Agent hooks for sessions.\n---\n\n# Create Hook\n\nBody\n"
)


def _match(
    *,
    name: str | None = "create-hook",
    markdown: str = _FULL_MARKDOWN,
) -> MatchedSkill:
    return MatchedSkill(
        doc_id="create-hook",
        file_path="/home/user/skills/create-hook.md",
        markdown=markdown,
        name=name,
        score=1.0,
        token_count=10,
    )


def test_frontmatter_search_text_collects_name_description_and_other_strings() -> None:
    frontmatter = "---\nname: create-hook\ndescription: Agent hooks.\nlicense: MIT\n---"
    text = frontmatter_search_text(frontmatter)
    assert text.splitlines() == ["create-hook", "Agent hooks.", "MIT"]


def test_frontmatter_search_text_returns_empty_without_frontmatter() -> None:
    assert frontmatter_search_text(None) == ""
    assert frontmatter_search_text("") == ""
    assert frontmatter_search_text("---\ndescription:\n---") == ""


def test_skill_name_from_frontmatter_returns_trimmed_name() -> None:
    frontmatter = "---\nname: create-hook\ndescription: hooks\n---"
    assert skill_name_from_frontmatter(frontmatter) == "create-hook"


def test_skill_name_from_frontmatter_rejects_missing_or_blank_name() -> None:
    assert skill_name_from_frontmatter(None) is None
    assert skill_name_from_frontmatter("") is None
    assert skill_name_from_frontmatter("---\ndescription: only\n---") is None
    assert skill_name_from_frontmatter("---\nname:\n---") is None
    assert skill_name_from_frontmatter("---\nname:   \t  \n---") is None


def test_injection_markdown_body_strips_frontmatter_and_keeps_body() -> None:
    body = injection_markdown_body(_FULL_MARKDOWN)
    assert body.startswith("# Create Hook")
    assert "description:" not in body
    assert "name:" not in body
    assert "Body" in body


def test_format_agent_skills_uses_skill_tag_with_name_and_path() -> None:
    injected = format_agent_skills([_match(name="create-hook")])
    assert '<skill name="create-hook" path="/home/user/skills/create-hook.md">' in injected
    assert "</skill>" in injected
    assert "<file " not in injected
    assert "name: create-hook" not in injected
    assert "# Create Hook" in injected
    assert "Body" in injected
    assert "description:" not in injected


def test_format_agent_skills_omits_name_attribute_but_keeps_body_when_missing() -> None:
    injected = format_agent_skills([_match(name=None)])
    assert '<skill path="/home/user/skills/create-hook.md">' in injected
    assert 'name="' not in injected
    assert "# Create Hook" in injected
    assert "Body" in injected
    assert "description:" not in injected

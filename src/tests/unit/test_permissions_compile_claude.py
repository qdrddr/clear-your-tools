"""Tests for Claude permissions export compile."""

from __future__ import annotations

from cyt.permissions.compile_claude import compile_claude_permissions
from cyt.permissions.schema import EffectivePermissions, McpPermissions, SkillsPermissions


def test_compile_claude_permissions_formats_entries() -> None:
    effective = EffectivePermissions(
        mcp=McpPermissions(deny=("jcodemunch", "fff/find_files", "ctx/*"), allow=()),
        skills=SkillsPermissions(deny=("noisy-skill",), allow=()),
    )
    compiled = compile_claude_permissions(effective)
    assert compiled["deny"] == [
        "mcp__jcodemunch",
        "mcp__fff__find_files",
        "mcp__ctx__*",
        "Skill(noisy-skill)",
    ]
    assert compiled["allow"] == []


def test_compile_claude_permissions_skips_path_based_skill_rules() -> None:
    effective = EffectivePermissions(
        mcp=McpPermissions(deny=(), allow=()),
        skills=SkillsPermissions(
            deny=("named-skill", "path:.cursor/skills/foo"),
            allow=(),
        ),
    )
    compiled = compile_claude_permissions(effective)
    assert compiled["deny"] == ["Skill(named-skill)"]

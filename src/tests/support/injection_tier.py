"""Helpers to stamp injection tiers on hook test fixtures."""

from __future__ import annotations

from typing import Any

from cyt.skills.search import MatchedSkill


def stamp_tool_tier(tool: dict[str, Any], *, tier: str = "t3") -> dict[str, Any]:
    """Return *tool* with ``cyt_injection_tier`` set for tier-aware XML formatting."""
    stamped = dict(tool)
    stamped["cyt_injection_tier"] = tier
    return stamped


def stamp_tools_tier(tools: list[dict[str, Any]], *, tier: str = "t3") -> list[dict[str, Any]]:
    return [stamp_tool_tier(tool, tier=tier) for tool in tools]


def stamp_skill_tier(match: MatchedSkill, *, tier: str = "t3") -> MatchedSkill:
    return MatchedSkill(
        doc_id=match.doc_id,
        file_path=match.file_path,
        markdown=match.markdown,
        name=match.name,
        score=match.score,
        token_count=match.token_count,
        command=match.command,
        injection_tier=tier,
    )

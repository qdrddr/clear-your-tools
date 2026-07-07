"""Filter injection survivors already present verbatim in session text."""

from __future__ import annotations

from typing import Any

from cyt.skills.inject import format_skill_item
from cyt.skills.search import MatchedSkill
from cyt.tools.inject import format_tool_item


def is_pre_exposed(fragment: str, session_text: str) -> bool:
    if not fragment.strip() or not session_text.strip():
        return False
    return fragment in session_text


def filter_pre_exposed_tools(
    tools: list[dict[str, Any]],
    session_text: str,
    *,
    include_tool_description: bool = True,
) -> list[dict[str, Any]]:
    if not session_text.strip():
        return list(tools)
    kept: list[dict[str, Any]] = []
    for tool in tools:
        fragment = format_tool_item(tool, include_tool_description=include_tool_description)
        if fragment and is_pre_exposed(fragment, session_text):
            continue
        kept.append(tool)
    return kept


def filter_pre_exposed_skills(
    matches: list[MatchedSkill],
    session_text: str,
) -> list[MatchedSkill]:
    if not session_text.strip():
        return list(matches)
    kept: list[MatchedSkill] = []
    for match in matches:
        fragment = format_skill_item(match)
        if fragment and is_pre_exposed(fragment, session_text):
            continue
        kept.append(match)
    return kept

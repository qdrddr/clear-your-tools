"""Filter injection survivors already present verbatim in session text.

Pre-exposes in-session verbatim survivors of injections.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from cyt.resources.inject import MatchedResource, format_resource_item
from cyt.skills.inject import format_skill_item
from cyt.skills.search import MatchedSkill
from cyt.tools.inject import _xml_single_quoted_attr, format_tool_item

_SKILL_COMMAND_ATTR = re.compile(
    r"command\s*=\s*['\"](?P<value>(?:\\.|[^'\"\\])*)['\"]",
    re.IGNORECASE,
)


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


def _skill_command_pre_exposed(command: str, session_text: str) -> bool:
    text = command.strip()
    if not text:
        return False
    if text in session_text:
        return True
    escaped = _xml_single_quoted_attr(text)
    if f"command='{escaped}'" in session_text:
        return True
    for match in _SKILL_COMMAND_ATTR.finditer(session_text):
        if match.group("value") in {text, escaped}:
            return True
    return False


def filter_pre_exposed_skills(
    matches: Sequence[MatchedSkill],
    session_text: str,
) -> list[MatchedSkill]:
    if not session_text.strip():
        return list(matches)
    kept: list[MatchedSkill] = []
    for match in matches:
        fragment = format_skill_item(match)
        if fragment and is_pre_exposed(fragment, session_text):
            continue
        if match.command and _skill_command_pre_exposed(match.command, session_text):
            continue
        kept.append(match)
    return kept


def filter_pre_exposed_resources(
    matches: list[MatchedResource],
    session_text: str,
) -> list[MatchedResource]:
    if not session_text.strip():
        return list(matches)
    kept: list[MatchedResource] = []
    for match in matches:
        fragment = format_resource_item(match)
        if fragment and is_pre_exposed(fragment, session_text):
            continue
        if _skill_command_pre_exposed(match.command, session_text):
            continue
        kept.append(match)
    return kept

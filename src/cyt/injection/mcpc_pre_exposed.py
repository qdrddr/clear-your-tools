"""Granular MCPC pre-exposure detection and tool filtering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from cyt.injection.pre_exposed import is_pre_exposed
from cyt.tools.inject import _xml_single_quoted_attr


@dataclass(frozen=True)
class McpcPreExposureFlags:
    omit_agent_tools_description: bool
    omit_server_instructions: frozenset[str]
    omit_server_description: frozenset[str]
    omit_tool_description: frozenset[tuple[str, str]]


def _plain_or_escaped_attr_pre_exposed(session_text: str, attr_name: str, value: str) -> bool:
    text = value.strip()
    if not text or not session_text.strip():
        return False
    if text in session_text:
        return True
    escaped = _xml_single_quoted_attr(text)
    needle = f"{attr_name}='{escaped}'"
    if needle in session_text:
        return True
    pattern = re.compile(
        rf"\b{re.escape(attr_name)}\s*=\s*['\"](?:{re.escape(text)}|{re.escape(escaped)})['\"]",
        re.IGNORECASE,
    )
    return bool(pattern.search(session_text))


def _agent_tools_description_pre_exposed(session_text: str) -> bool:
    from cyt.injection.header_pre_exposed import agent_tools_intro_pre_exposed
    from cyt.tools.mcpc_inject import _mcpc_agent_tools_description

    intro = _mcpc_agent_tools_description()
    return agent_tools_intro_pre_exposed(session_text, intro)


def _server_attr_pre_exposed(
    session_text: str,
    *,
    server_name: str,
    attr_name: str,
    value: str,
) -> bool:
    if not value.strip():
        return False
    if _plain_or_escaped_attr_pre_exposed(session_text, attr_name, value):
        return True
    escaped_name = _xml_single_quoted_attr(server_name)
    escaped_value = _xml_single_quoted_attr(value)
    server_pattern = re.compile(
        rf"<server[^>]*\bname\s*=\s*['\"]{re.escape(escaped_name)}['\"][^>]*\b{attr_name}\s*=\s*['\"]{re.escape(escaped_value)}['\"]",
        re.IGNORECASE,
    )
    if server_pattern.search(session_text):
        return True
    server_pattern_plain = re.compile(
        rf"<server[^>]*\bname\s*=\s*['\"]{re.escape(server_name)}['\"][^>]*\b{attr_name}\s*=\s*['\"]{re.escape(value)}['\"]",
        re.IGNORECASE,
    )
    return bool(server_pattern_plain.search(session_text))


def compute_mcpc_pre_exposure_flags(
    tools: list[dict[str, Any]],
    session_text: str,
) -> McpcPreExposureFlags:
    if not session_text.strip():
        return McpcPreExposureFlags(
            omit_agent_tools_description=False,
            omit_server_instructions=frozenset(),
            omit_server_description=frozenset(),
            omit_tool_description=frozenset(),
        )

    omit_instructions: set[str] = set()
    omit_descriptions: set[str] = set()
    omit_tool_descriptions: set[tuple[str, str]] = set()
    seen_sessions: set[str] = set()

    for tool in tools:
        session = str(tool.get("mcpc_session") or "").strip()
        server_name = str(tool.get("server_name") or session).strip()
        if session and session not in seen_sessions:
            seen_sessions.add(session)
            instructions = str(tool.get("server_instructions") or "").strip()
            if instructions and _server_attr_pre_exposed(
                session_text,
                server_name=server_name,
                attr_name="instructions",
                value=instructions,
            ):
                omit_instructions.add(session)
            server_description = str(tool.get("server_description") or "").strip()
            if server_description and _server_attr_pre_exposed(
                session_text,
                server_name=server_name,
                attr_name="description",
                value=server_description,
            ):
                omit_descriptions.add(session)

        tool_name = str(tool.get("tool_name") or tool.get("name") or "").strip()
        description = str(tool.get("description") or "").strip()
        if session and tool_name and description:
            if _plain_or_escaped_attr_pre_exposed(session_text, "description", description):
                omit_tool_descriptions.add((session, tool_name))

    return McpcPreExposureFlags(
        omit_agent_tools_description=_agent_tools_description_pre_exposed(session_text),
        omit_server_instructions=frozenset(omit_instructions),
        omit_server_description=frozenset(omit_descriptions),
        omit_tool_description=frozenset(omit_tool_descriptions),
    )


def filter_pre_exposed_mcpc_tools(
    tools: list[dict[str, Any]],
    session_text: str,
) -> list[dict[str, Any]]:
    """Drop tools whose full MCPC fragment is verbatim in session text."""
    if not session_text.strip():
        return list(tools)
    from cyt.tools.mcpc_inject import _format_mcpc_tool_item

    kept: list[dict[str, Any]] = []
    for tool in tools:
        fragment = _format_mcpc_tool_item(tool)
        if fragment and is_pre_exposed(fragment, session_text):
            continue
        kept.append(tool)
    return kept

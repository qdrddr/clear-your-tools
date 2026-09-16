"""Detect pre-exposed injection header prose (agent-tools path/intro, cyt-mcp note)."""

from __future__ import annotations

import re

from cyt.tools.inject import _xml_single_quoted_attr

_AGENT_TOOLS_BLOCK_RE = re.compile(
    r"<agent-tools[^>]*>.*?</agent-tools>",
    re.DOTALL | re.IGNORECASE,
)
_AGENT_TOOLS_OPEN_TAG_RE = re.compile(r"<agent-tools(\s[^>]*)?>", re.IGNORECASE)
_CYT_MCP_BLOCK_RE = re.compile(r"<cyt-mcp[^>]*>.*?</cyt-mcp>", re.DOTALL | re.IGNORECASE)
_AGENT_SKILLS_BLOCK_RE = re.compile(
    r"<agent-skills[^>]*>.*?</agent-skills>",
    re.DOTALL | re.IGNORECASE,
)


def _plain_or_escaped_attr_pre_exposed(session_text: str, attr_name: str, value: str) -> bool:
    text = value.strip()
    if not text or not session_text.strip():
        return False
    escaped = _xml_single_quoted_attr(text)
    needle = f"{attr_name}='{escaped}'"
    if needle in session_text:
        return True
    pattern = re.compile(
        rf"\b{re.escape(attr_name)}\s*=\s*['\"](?:{re.escape(text)}|{re.escape(escaped)})['\"]",
        re.IGNORECASE,
    )
    return bool(pattern.search(session_text))


def _text_in_xml_block(session_text: str, block_pattern: re.Pattern[str], text: str) -> bool:
    for match in block_pattern.finditer(session_text):
        if text in match.group(0):
            return True
    return False


def intro_text_pre_exposed(session_text: str, intro: str) -> bool:
    """True when *intro* already appears in session (inner text or legacy attribute)."""
    text = intro.strip()
    if not text or not session_text.strip():
        return False
    if _plain_or_escaped_attr_pre_exposed(session_text, "description", text):
        return True
    return _text_in_xml_block(session_text, _AGENT_TOOLS_BLOCK_RE, text)


def agent_tools_intro_pre_exposed(session_text: str, intro: str) -> bool:
    return intro_text_pre_exposed(session_text, intro)


def agent_tools_path_pre_exposed(session_text: str, path: str) -> bool:
    """True when the same workspace path already appears on an ``<agent-tools>`` open tag."""
    text = path.strip()
    if not text or not session_text.strip():
        return False
    emitted = f"<agent-tools path='{_xml_single_quoted_attr(text)}'>"
    if emitted in session_text:
        return True
    for match in _AGENT_TOOLS_OPEN_TAG_RE.finditer(session_text):
        open_tag = match.group(0)
        escaped = _xml_single_quoted_attr(text)
        if f"path='{escaped}'" in open_tag:
            return True
        pattern = re.compile(
            rf"\bpath\s*=\s*['\"](?:{re.escape(text)}|{re.escape(escaped)})['\"]",
            re.IGNORECASE,
        )
        if pattern.search(open_tag):
            return True
    return False


def cyt_mcp_note_pre_exposed(session_text: str, note: str) -> bool:
    text = note.strip()
    if not text or not session_text.strip():
        return False
    if _text_in_xml_block(session_text, _CYT_MCP_BLOCK_RE, text):
        return True
    return _plain_or_escaped_attr_pre_exposed(session_text, "description", text)


def tool_tier_legend_pre_exposed(session_text: str, legend: str) -> bool:
    text = legend.strip()
    if not text or not session_text.strip():
        return False
    return _text_in_xml_block(session_text, _CYT_MCP_BLOCK_RE, text)


def skill_tier_legend_pre_exposed(session_text: str, legend: str) -> bool:
    text = legend.strip()
    if not text or not session_text.strip():
        return False
    return _text_in_xml_block(session_text, _AGENT_SKILLS_BLOCK_RE, text)

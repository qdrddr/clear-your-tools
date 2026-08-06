"""Merge proxy injection XML blocks without duplicating wrappers."""

from __future__ import annotations

import re
from collections.abc import Callable

from cyt.tools.inject import ensure_agent_tools_starts_on_new_line

_SKILLS_BLOCK_RE = re.compile(
    r"(?:Based on the user query[^\n]*\n\n)?<agent-skills[^>]*>.*?</agent-skills>",
    re.DOTALL,
)
_TOOLS_BLOCK_RE = re.compile(
    r"\n?<agent-tools[^>]*>.*?</agent-tools>",
    re.DOTALL,
)
_SKILL_ITEM_RE = re.compile(r"<skill[^>]*>.*?</skill>", re.DOTALL)
_TOOL_ITEM_RE = re.compile(r"<tool[^>]*>.*?</tool>", re.DOTALL)
_INNER_SOURCE_TAGS = ("mcpc", "executor", "definitions", "cyt_mcp", "cloudflare")
_INNER_SOURCE_RE = {
    tag: re.compile(rf"<{tag}[^>]*>.*?</{tag}>", re.DOTALL) for tag in _INNER_SOURCE_TAGS
}
_SKILL_NAME_RE = re.compile(r'name="([^"]*)"')
_SKILL_PATH_RE = re.compile(r'path="([^"]*)"')
_SKILL_COMMAND_RE = re.compile(r"command='([^']*)'")
_TOOL_NAME_RE = re.compile(r"name='([^']*)'")


def _extract_block(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(0).strip() if match else ""


def _extract_source_section(tag: str, text: str) -> str:
    pattern = _INNER_SOURCE_RE.get(tag)
    if pattern is None:
        return ""
    match = pattern.search(text)
    return match.group(0).strip() if match else ""


def _skill_item_key(item: str) -> str:
    for pattern in (_SKILL_NAME_RE, _SKILL_PATH_RE, _SKILL_COMMAND_RE):
        match = pattern.search(item)
        if match:
            return match.group(1)
    return item.strip()


def _tool_item_key(item: str) -> str:
    match = _TOOL_NAME_RE.search(item)
    return match.group(1) if match else item.strip()


def _skills_intro(block: str) -> str:
    match = _SKILLS_BLOCK_RE.search(block)
    if not match:
        return ""
    prefix = block[: match.start()].strip()
    if prefix.startswith("Based on the user query"):
        return prefix
    return ""


def _merge_inner_source_sections(
    prior_block: str,
    delta_block: str,
    *,
    inner_source_tags: tuple[str, ...],
    close_tag: str,
) -> str | None:
    open_match = re.match(r"(<agent-tools[^>]*>)", delta_block) or re.match(
        r"(<agent-tools[^>]*>)",
        prior_block,
    )
    open_tag = open_match.group(1) if open_match else "<agent-tools>"
    merged_inner: list[str] = []
    for tag in inner_source_tags:
        section = _extract_source_section(tag, delta_block) or _extract_source_section(
            tag,
            prior_block,
        )
        if section:
            merged_inner.append(section)
    if not merged_inner:
        return None
    return "\n".join([open_tag, *merged_inner, close_tag])


def _merge_keyed_item_blocks(
    prior_block: str,
    delta_block: str,
    *,
    item_pattern: re.Pattern[str],
    item_key: Callable[[str], str],
    open_tag_pattern: re.Pattern[str],
    close_tag: str,
) -> str:
    open_match = open_tag_pattern.search(delta_block) or open_tag_pattern.search(prior_block)
    open_tag = open_match.group(0) if open_match else delta_block.split("\n", 1)[0]

    merged_by_key: dict[str, str] = {}
    order: list[str] = []
    for item in item_pattern.findall(prior_block):
        key = item_key(item)
        if key not in merged_by_key:
            order.append(key)
        merged_by_key[key] = item
    for item in item_pattern.findall(delta_block):
        key = item_key(item)
        if key not in merged_by_key:
            order.append(key)
        merged_by_key[key] = item

    body_lines = [merged_by_key[key] for key in order]
    if not body_lines:
        delta_body = re.sub(r"^<agent-tools[^>]*>\s*", "", delta_block)
        delta_body = re.sub(r"\s*</agent-tools>\s*$", "", delta_body)
        prior_body = re.sub(r"^<agent-tools[^>]*>\s*", "", prior_block)
        prior_body = re.sub(r"\s*</agent-tools>\s*$", "", prior_body)
        body = delta_body.strip() or prior_body.strip()
        if not body:
            return delta_block or prior_block
        return "\n".join([open_tag, body, close_tag])

    return "\n".join([open_tag, *body_lines, close_tag])


def _merge_item_blocks(
    prior_block: str,
    delta_block: str,
    *,
    item_pattern: re.Pattern[str],
    item_key: Callable[[str], str],
    open_tag_pattern: re.Pattern[str],
    close_tag: str,
    inner_source_tags: tuple[str, ...] = (),
) -> str:
    prior_block = prior_block.strip()
    delta_block = delta_block.strip()
    if not prior_block:
        return delta_block
    if not delta_block:
        return prior_block

    if inner_source_tags:
        merged = _merge_inner_source_sections(
            prior_block,
            delta_block,
            inner_source_tags=inner_source_tags,
            close_tag=close_tag,
        )
        if merged is not None:
            return merged

    return _merge_keyed_item_blocks(
        prior_block,
        delta_block,
        item_pattern=item_pattern,
        item_key=item_key,
        open_tag_pattern=open_tag_pattern,
        close_tag=close_tag,
    )


def merge_agent_skills_blocks(prior_block: str, delta_block: str) -> str:
    prior_block = prior_block.strip()
    delta_block = delta_block.strip()
    if not prior_block:
        return delta_block
    if not delta_block:
        return prior_block

    merged_by_key: dict[str, str] = {}
    order: list[str] = []
    for item in _SKILL_ITEM_RE.findall(prior_block):
        key = _skill_item_key(item)
        if key not in merged_by_key:
            order.append(key)
        merged_by_key[key] = item
    for item in _SKILL_ITEM_RE.findall(delta_block):
        key = _skill_item_key(item)
        if key not in merged_by_key:
            order.append(key)
        merged_by_key[key] = item

    intro = _skills_intro(delta_block) or _skills_intro(prior_block)
    lines: list[str] = []
    if intro:
        lines.extend([intro, ""])
    lines.append("<agent-skills>")
    lines.extend(merged_by_key[key] for key in order)
    lines.append("</agent-skills>")
    return "\n".join(lines)


def merge_agent_tools_blocks(prior_block: str, delta_block: str) -> str:
    merged = _merge_item_blocks(
        prior_block,
        delta_block,
        item_pattern=_TOOL_ITEM_RE,
        item_key=_tool_item_key,
        open_tag_pattern=re.compile(r"<agent-tools[^>]*>"),
        close_tag="</agent-tools>",
        inner_source_tags=_INNER_SOURCE_TAGS,
    )
    return ensure_agent_tools_starts_on_new_line(merged)


def injection_domain(text: str) -> str | None:
    if "<agent-skills" in text:
        return "skills"
    if "<agent-tools" in text:
        return "tools"
    return None


def merge_injection_into_text(existing: str, delta: str, *, same_turn: bool = True) -> str:
    """Merge or replace delta injection into existing turn text."""
    delta = delta.strip()
    if not delta:
        return existing
    if not existing.strip():
        return delta

    delta_domain = injection_domain(delta)
    if delta_domain == "skills" and not same_turn:
        cleaned = strip_agent_skills_blocks(existing)
        if cleaned:
            return cleaned + "\n\n" + delta
        return delta

    if delta_domain == "skills":
        delta_block = _extract_block(_SKILLS_BLOCK_RE, delta)
        prior_block = _extract_block(_SKILLS_BLOCK_RE, existing)
        if delta_block and prior_block:
            merged = merge_agent_skills_blocks(prior_block, delta_block)
            return existing.replace(prior_block, merged, 1)
    elif delta_domain == "tools":
        delta_block = _extract_block(_TOOLS_BLOCK_RE, delta)
        prior_block = _extract_block(_TOOLS_BLOCK_RE, existing)
        if delta_block and prior_block:
            merged = merge_agent_tools_blocks(prior_block, delta_block)
            return existing.replace(prior_block, merged, 1)

    if existing.endswith("\n"):
        return existing + "\n" + delta
    return existing + "\n\n" + ensure_agent_tools_starts_on_new_line(delta, after=existing)


def strip_agent_skills_blocks(text: str) -> str:
    """Remove all ``<agent-skills>`` blocks (and CYT intro) from text."""
    if not text.strip():
        return ""
    cleaned = _SKILLS_BLOCK_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()

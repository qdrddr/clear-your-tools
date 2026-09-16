"""Format <agent-skills> injection blocks."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

import yaml

from cyt.common.paths import shorten_home_path
from cyt.indexer.tokens import count_tokens
from cyt.skills.frontmatter import injection_markdown_body
from cyt.skills.search import MatchedSkill
from cyt.tools.inject import TIER_GROUP_ORDER, _format_tier_group

_INTRO_SKINNY = (
    "Based on the user query added chunks of descriptions of skills (not entire skill). "
    "The entire skill could be retrieved with the file path, though in most cases it likely "
    "excessive."
)

_INTRO_FULL = (
    "Based on the user query, complete skill content is injected below. "
    "Do not use Read on the skill file paths; the injected content is authoritative for this turn."
)

_INTRO = _INTRO_SKINNY


def skills_inject_intro(*, full: bool = False) -> str:
    return _INTRO_FULL if full else _INTRO_SKINNY


def format_agent_skills_empty(*, combined_text: str = "") -> str:
    """Stable ``<agent-skills>`` wrapper with tier legend when no skills inject."""
    from cyt.injection.header_pre_exposed import skill_tier_legend_pre_exposed
    from cyt.injection.tier_legend import SKILL_TIER_LEGEND

    if skill_tier_legend_pre_exposed(combined_text, SKILL_TIER_LEGEND):
        return ""
    return "\n".join(["<agent-skills>", SKILL_TIER_LEGEND, "</agent-skills>"])


def _parsed_frontmatter(markdown: str) -> dict[str, object]:
    text = markdown.strip()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    yaml_body = text[3:end].strip()
    if not yaml_body:
        return {}
    try:
        parsed = yaml.safe_load(yaml_body)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_skill_command(match: MatchedSkill) -> str | None:
    if match.command:
        return match.command
    frontmatter = _parsed_frontmatter(match.markdown)
    command = frontmatter.get("mcpc_command")
    if isinstance(command, str) and command.strip():
        return command.strip()
    return None


def _skill_open_tag(
    path: str,
    name: str | None,
    *,
    command: str | None = None,
) -> str:
    if command:
        attrs = []
        if name:
            attrs.append(f'name="{name}"')
        attrs.append(f"command='{command}'")
        return f"<skill {' '.join(attrs)}>"
    if name:
        return f'<skill name="{name}" path="{path}">'
    return f'<skill path="{path}">'


def injection_tier_for_skill(match: MatchedSkill) -> str | None:
    """Return ``injection_tier`` (t0-t4) stamped by the tier manager for grouping."""
    tier = match.injection_tier
    if not isinstance(tier, str) or not tier.strip():
        return "t3"
    label = tier.strip().lower()
    if label in TIER_GROUP_ORDER:
        return label
    return "t3"


def format_skills_grouped_by_tier(
    matches: list[MatchedSkill],
    *,
    full_flags: dict[str, bool] | None = None,
    format_item: Callable[..., str] | None = None,
) -> str:
    """Format skills wrapped in ``<tier_tN>`` groups (t0-t4 only; skip skills without a tier)."""
    from cyt.injection.session_log_build import skill_item_key

    render = format_item or format_skill_item
    buckets: OrderedDict[str, list[str]] = OrderedDict((tier, []) for tier in TIER_GROUP_ORDER)

    for match in matches:
        tier = injection_tier_for_skill(match)
        if tier is None:
            continue
        command = _resolve_skill_command(match)
        key = skill_item_key(match, command=command)
        full = bool(full_flags.get(key)) if full_flags else False
        item = render(match, full=full)
        if not item:
            continue
        buckets[tier].append(item)

    blocks: list[str] = []
    for tier in TIER_GROUP_ORDER:
        items = buckets[tier]
        if items:
            blocks.append(_format_tier_group(tier, items))
    return "\n".join(blocks)


def _skill_has_injection_body(match: MatchedSkill) -> bool:
    return bool(injection_markdown_body(match.markdown).strip())


def _display_skill_path(file_path: str) -> str:
    text = file_path.strip()
    if not text:
        return text
    if text.startswith("~/") or (len(text) >= 2 and text[1] == ":") or Path(text).is_absolute():
        return Path(shorten_home_path(text)).as_posix()
    return text.replace("\\", "/")


def format_skill_item(match: MatchedSkill, *, full: bool = False) -> str:
    """Format a single ``<skill>…</skill>`` block (no ``<agent-skills>`` wrapper)."""
    from cyt.tools.inject import _xml_single_quoted_attr

    path = _display_skill_path(match.file_path)
    if full:
        body = match.markdown.rstrip()
    else:
        body = injection_markdown_body(match.markdown).rstrip()
    if not body:
        return ""
    command = _resolve_skill_command(match)
    if command:
        command = _xml_single_quoted_attr(command)
    return "\n".join(
        [
            _skill_open_tag(path, match.name, command=command),
            body,
            "</skill>",
        ],
    )


def format_agent_skills(
    matches: list[MatchedSkill],
    *,
    full_flags: dict[str, bool] | None = None,
    combined_text: str = "",
) -> str:
    if not matches:
        return ""
    from cyt.injection.session_log_build import skill_item_key
    from cyt.skills.inject import _resolve_skill_command

    injectable = [match for match in matches if _skill_has_injection_body(match) or full_flags]
    if not injectable:
        injectable = list(matches)
    eligible: list[MatchedSkill] = []
    emitted_full_flags: list[bool] = []
    for match in injectable:
        command = _resolve_skill_command(match)
        key = skill_item_key(match, command=command)
        full = bool(full_flags.get(key)) if full_flags else False
        if not full and not _skill_has_injection_body(match):
            continue
        eligible.append(match)
        emitted_full_flags.append(full)
    if not eligible:
        return ""
    body = format_skills_grouped_by_tier(eligible, full_flags=full_flags)
    if not body.strip():
        return ""
    from cyt.injection.header_pre_exposed import skill_tier_legend_pre_exposed
    from cyt.injection.pre_exposed import is_pre_exposed
    from cyt.injection.tier_legend import SKILL_TIER_LEGEND

    intro = skills_inject_intro(full=bool(emitted_full_flags) and all(emitted_full_flags))
    include_intro = not (combined_text.strip() and is_pre_exposed(intro, combined_text))
    include_tier_legend = not skill_tier_legend_pre_exposed(combined_text, SKILL_TIER_LEGEND)
    inner_lines: list[str] = []
    if include_tier_legend:
        inner_lines.append(SKILL_TIER_LEGEND)
    inner_lines.append(body)
    if include_intro:
        return "\n".join([intro, "", "<agent-skills>", *inner_lines, "</agent-skills>"])
    return "\n".join(["<agent-skills>", *inner_lines, "</agent-skills>"])


def injection_token_count(matches: list[MatchedSkill] | str) -> int:
    if isinstance(matches, str):
        return count_tokens(matches)
    return count_tokens(format_agent_skills(matches))

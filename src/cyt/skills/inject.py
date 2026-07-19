"""Format <agent-skills> injection blocks."""

from __future__ import annotations

import yaml

from cyt.common.paths import shorten_home_path
from cyt.indexer.tokens import count_tokens
from cyt.skills.frontmatter import injection_markdown_body
from cyt.skills.search import MatchedSkill

_INTRO = (
    "Based on the user query added chunks of descriptions of skills (not entire skill). "
    "The entire skill could be retrieved with the file path, though in most cases it likely "
    "excessive."
)


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


def _skill_open_tag(path: str, name: str | None, *, command: str | None = None) -> str:
    if command:
        attrs = []
        if name:
            attrs.append(f'name="{name}"')
        attrs.append(f"command='{command}'")
        return f"<skill {' '.join(attrs)}>"
    if name:
        return f'<skill name="{name}" path="{path}">'
    return f'<skill path="{path}">'


def _skill_has_injection_body(match: MatchedSkill) -> bool:
    return bool(injection_markdown_body(match.markdown).strip())


def format_skill_item(match: MatchedSkill) -> str:
    """Format a single ``<skill>…</skill>`` block (no ``<agent-skills>`` wrapper)."""
    from cyt.tools.inject import _xml_single_quoted_attr

    path = shorten_home_path(match.file_path)
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


def format_agent_skills(matches: list[MatchedSkill]) -> str:
    if not matches:
        return ""
    injectable = [match for match in matches if _skill_has_injection_body(match)]
    if not injectable:
        return ""
    item_lines = [format_skill_item(match) for match in injectable]
    if not any(item_lines):
        return ""
    lines = [_INTRO, "", "<agent-skills>", *item_lines, "</agent-skills>"]
    return "\n".join(lines)


def injection_token_count(matches: list[MatchedSkill] | str) -> int:
    if isinstance(matches, str):
        return count_tokens(matches)
    return count_tokens(format_agent_skills(matches))

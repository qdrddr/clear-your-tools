"""Format <agent-skills> injection blocks."""

from __future__ import annotations

from cyt.common.paths import shorten_home_path
from cyt.indexer.tokens import count_tokens
from cyt.skills.frontmatter import injection_markdown_body
from cyt.skills.search import MatchedSkill

_INTRO = (
    "Based on the user query added chunks of descriptions of skills (not entire skill). "
    "The entire skill could be retrieved with the file path, though in most cases it likely "
    "excessive."
)


def _skill_open_tag(path: str, name: str | None) -> str:
    if name:
        return f'<skill name="{name}" path="{path}">'
    return f'<skill path="{path}">'


def format_skill_item(match: MatchedSkill) -> str:
    """Format a single ``<skill>…</skill>`` block (no ``<agent-skills>`` wrapper)."""
    path = shorten_home_path(match.file_path)
    return "\n".join(
        [
            _skill_open_tag(path, match.name),
            injection_markdown_body(match.markdown).rstrip(),
            "</skill>",
        ],
    )


def format_agent_skills(matches: list[MatchedSkill]) -> str:
    if not matches:
        return ""
    item_lines = [format_skill_item(match) for match in matches]
    if not any(item_lines):
        return ""
    lines = [_INTRO, "", "<agent-skills>", *item_lines, "</agent-skills>"]
    return "\n".join(lines)


def injection_token_count(matches: list[MatchedSkill] | str) -> int:
    if isinstance(matches, str):
        return count_tokens(matches)
    return count_tokens(format_agent_skills(matches))

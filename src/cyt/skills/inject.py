"""Format <agent-skills> injection blocks."""

from __future__ import annotations

import os
from pathlib import Path

from cyt.indexer.tokens import count_tokens
from cyt.skills.search import MatchedSkill

_INTRO = (
    "Based on the user query added chunks of descriptions of skills (not entire skill). "
    "The entire skill could be retrieved with the file path, though in most cases it likely "
    "excessive."
)


def shorten_home_path(path: str) -> str:
    expanded = Path(path).expanduser()
    home = Path.home()
    try:
        rel = expanded.relative_to(home)
        return f"~/{rel.as_posix()}"
    except ValueError:
        text = str(expanded)
        home_env = os.environ.get("HOME")
        if home_env and text.startswith(home_env.rstrip("/") + "/"):
            return "~/" + text[len(home_env.rstrip("/")) + 1 :]
        return text


def format_agent_skills(matches: list[MatchedSkill]) -> str:
    if not matches:
        return ""
    lines = [_INTRO, "", "<agent-skills>"]
    for match in matches:
        path = shorten_home_path(match.file_path)
        lines.append(f'<file path="{path}">')
        lines.append(match.markdown.rstrip())
        lines.append("</file>")
    lines.append("</agent-skills>")
    return "\n".join(lines)


def injection_token_count(text: str) -> int:
    return count_tokens(text)

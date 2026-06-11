"""Token budget helpers for skill search results."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cyt.config import skills_max_tokens_per_request
from cyt.skills.search import MatchedSkill


def cap_matched_skills_by_tokens(
    matched: list[MatchedSkill],
    *,
    config: dict[str, Any] | None = None,
    sort_key: Callable[[MatchedSkill], Any] | None = None,
    reverse: bool = False,
) -> list[MatchedSkill]:
    """Sort matched skills, then drop lowest-priority entries until within token budget."""
    if sort_key is not None:
        matched.sort(key=sort_key, reverse=reverse)

    max_tokens = skills_max_tokens_per_request(config)
    total_tokens = sum(item.token_count for item in matched)
    while matched and total_tokens > max_tokens:
        dropped = matched.pop()
        total_tokens -= dropped.token_count
    return matched

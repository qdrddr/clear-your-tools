"""Greedy skills selection within an injection token budget."""

from __future__ import annotations

from cyt.skills.search import MatchedSkill


def select_skills_within_budget(
    candidates: list[MatchedSkill],
    max_tokens: int,
) -> list[MatchedSkill]:
    """Greedy add by score with full recompose token measurement."""
    from cyt.skills.inject import format_agent_skills, injection_token_count

    if max_tokens <= 0 or not candidates:
        return []

    ordered = sorted(candidates, key=lambda item: item.score, reverse=True)
    selected: list[MatchedSkill] = []
    for candidate in ordered:
        trial = [*selected, candidate]
        injected = format_agent_skills(trial)
        if not injected:
            continue
        if injection_token_count(injected) <= max_tokens:
            selected = trial
    return selected

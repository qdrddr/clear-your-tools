"""Tests for greedy skills selection."""

from __future__ import annotations

from cyt.skills.search import MatchedSkill
from cyt.skills.select import select_skills_within_budget


def _skill(doc_id: str, tokens: int, score: float) -> MatchedSkill:
    body = "x" * max(tokens * 4, 8)
    return MatchedSkill(
        doc_id=doc_id,
        file_path=f"/tmp/{doc_id}.md",
        markdown=body,
        name=doc_id,
        score=score,
        token_count=tokens,
    )


def test_select_respects_max_tokens() -> None:
    candidates = [
        _skill("a", 100, 0.9),
        _skill("b", 100, 0.8),
        _skill("c", 100, 0.7),
    ]
    selected = select_skills_within_budget(candidates, max_tokens=150)
    assert len(selected) == 1
    assert selected[0].doc_id == "a"

"""Skills search dispatch (BM25 chunks, rerank nodes, or LLM nodes)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyt.config import skills_pipeline_uses_llm, skills_pipeline_uses_rerank
from cyt.skills.catalog import SkillEntryRef


@dataclass(frozen=True)
class MatchedSkill:
    doc_id: str
    file_path: str
    markdown: str
    name: str | None
    score: float
    token_count: int


def _frontmatter_by_doc(entries: list[SkillEntryRef]) -> dict[tuple[str, str], str | None]:
    result: dict[tuple[str, str], str | None] = {}
    for entry in entries:
        raw = entry.document.get("frontmatter")
        frontmatter = raw if isinstance(raw, str) else None
        result[(entry.entry_dir, entry.doc_id)] = frontmatter
    return result


def eligible_skills_after_gate(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> list[SkillEntryRef]:
    """Return skill entries that pass the BM25 frontmatter gate."""
    if not query.strip() or not entries:
        return []
    from cyt.skills.bm25 import excluded_by_frontmatter_gate

    excluded = excluded_by_frontmatter_gate(query, entries, config=config)
    return [entry for entry in entries if (entry.entry_dir, entry.doc_id) not in excluded]


def search_skills(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> list[MatchedSkill]:
    """Return matched skill reconstructions sorted by score (highest first)."""
    eligible = eligible_skills_after_gate(query, entries, config=config)
    if not eligible:
        return []

    if skills_pipeline_uses_rerank(config):
        from cyt.skills.rerank import rerank_skill_nodes

        matches, _usage = rerank_skill_nodes(query, eligible, config=config)
        return matches

    if skills_pipeline_uses_llm(config):
        from cyt.skills.llm import llm_skill_nodes

        matches, _usage = llm_skill_nodes(query, eligible, config=config)
        return matches

    from cyt.skills.bm25 import bm25_skill_chunks

    matches, _usage = bm25_skill_chunks(query, eligible, config=config)
    return matches

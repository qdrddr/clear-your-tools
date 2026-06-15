"""Skills search dispatch (BM25 chunks, rerank nodes, or LLM nodes)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cyt.config import (
    skills_bm25_node_fallback_threshold,
    skills_pipeline,
    skills_pipeline_uses_llm,
    skills_pipeline_uses_rerank,
)
from cyt.skills.catalog import SkillEntryRef
from cyt.skills.diagnostics import SearchItemRow, SkillsSearchTrace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchedSkill:
    doc_id: str
    file_path: str
    markdown: str
    name: str | None
    score: float
    token_count: int


@dataclass(frozen=True)
class SkillsPipelineRun:
    configured: str
    executed: str
    fallback_reason: str | None = None


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


def _skill_node_count(entries: list[SkillEntryRef]) -> int:
    from cyt.skills.nodes import build_skill_node_items

    return len(build_skill_node_items(entries))


def _should_use_bm25_fallback(entries: list[SkillEntryRef], config: dict[str, Any] | None) -> bool:
    threshold = skills_bm25_node_fallback_threshold(config)
    return _skill_node_count(entries) < threshold


def _bm25_node_fallback_reason(entries: list[SkillEntryRef], config: dict[str, Any] | None) -> str:
    threshold = skills_bm25_node_fallback_threshold(config)
    node_count = _skill_node_count(entries)
    return f"node count {node_count} below skills.bm25_node_fallback_threshold {threshold}"


def _run_bm25_pipeline(
    query: str,
    eligible: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> tuple[list[MatchedSkill], list[SearchItemRow], float]:
    from cyt.skills.bm25 import bm25_skill_chunks_with_trace

    matches, search_rows, threshold, _usage = bm25_skill_chunks_with_trace(
        query,
        eligible,
        config=config,
        max_tokens=max_tokens,
    )
    return matches, search_rows, threshold


def _run_search_pipeline_with_trace(
    query: str,
    eligible: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> tuple[list[MatchedSkill], SkillsPipelineRun, str, float | None, list[SearchItemRow]]:
    configured = skills_pipeline(config).strip().lower()

    if _should_use_bm25_fallback(eligible, config):
        reason = _bm25_node_fallback_reason(eligible, config)
        logger.debug("skills search falling back to BM25 (%s)", reason)
        matches, search_rows, threshold = _run_bm25_pipeline(
            query,
            eligible,
            config=config,
            max_tokens=max_tokens,
        )
        return (
            matches,
            SkillsPipelineRun(configured, "bm25", reason),
            "chunk",
            threshold,
            search_rows,
        )

    if skills_pipeline_uses_rerank(config):
        from cyt.skills.rerank import rerank_skill_nodes_with_trace

        try:
            matches, search_rows, threshold, _usage = rerank_skill_nodes_with_trace(
                query,
                eligible,
                config=config,
            )
            return (
                matches,
                SkillsPipelineRun(configured, "rerank"),
                "node",
                threshold,
                search_rows,
            )
        except Exception as exc:
            reason = f"rerank failed: {exc}"
            logger.warning("rerank skills search failed, falling back to BM25: %s", exc)
            matches, search_rows, threshold = _run_bm25_pipeline(
                query,
                eligible,
                config=config,
                max_tokens=max_tokens,
            )
            return (
                matches,
                SkillsPipelineRun(configured, "bm25", reason),
                "chunk",
                threshold,
                search_rows,
            )

    if skills_pipeline_uses_llm(config):
        from cyt.skills.llm import llm_skill_nodes_with_trace

        try:
            matches, search_rows, _usage = llm_skill_nodes_with_trace(
                query,
                eligible,
                config=config,
            )
            return (
                matches,
                SkillsPipelineRun(configured, "llm"),
                "node",
                None,
                search_rows,
            )
        except Exception as exc:
            reason = f"llm failed: {exc}"
            logger.warning("llm skills search failed, falling back to BM25: %s", exc)
            matches, search_rows, threshold = _run_bm25_pipeline(
                query,
                eligible,
                config=config,
                max_tokens=max_tokens,
            )
            return (
                matches,
                SkillsPipelineRun(configured, "bm25", reason),
                "chunk",
                threshold,
                search_rows,
            )

    matches, search_rows, threshold = _run_bm25_pipeline(
        query,
        eligible,
        config=config,
        max_tokens=max_tokens,
    )
    return matches, SkillsPipelineRun(configured, "bm25"), "chunk", threshold, search_rows


def _run_search_pipeline(
    query: str,
    eligible: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[MatchedSkill], SkillsPipelineRun]:
    matches, pipeline_run, _kind, _threshold, _rows = _run_search_pipeline_with_trace(
        query,
        eligible,
        config=config,
    )
    return matches, pipeline_run


def search_skills_with_trace(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> tuple[list[MatchedSkill], SkillsSearchTrace]:
    """Return matched skills plus frontmatter gate and search scoring diagnostics."""
    from cyt.skills.bm25 import frontmatter_gate_trace

    configured = skills_pipeline(config).strip().lower()
    frontmatter_rows, frontmatter_limit = frontmatter_gate_trace(query, entries, config=config)
    eligible = [
        entry
        for entry in entries
        if (entry.entry_dir, entry.doc_id)
        not in {(row.entry_dir, row.doc_id) for row in frontmatter_rows if not row.passed}
    ]
    if not eligible:
        return [], SkillsSearchTrace(
            frontmatter_limit=frontmatter_limit,
            frontmatter_rows=frontmatter_rows,
            pipeline_run=SkillsPipelineRun(
                configured,
                "",
                "no eligible skills after frontmatter gate",
            ),
            search_item_kind="",
            search_score_threshold=None,
            search_rows=[],
            matches=[],
        )

    candidates, pipeline_run, item_kind, threshold, search_rows = _run_search_pipeline_with_trace(
        query,
        eligible,
        config=config,
        max_tokens=max_tokens,
    )
    pre_budget_matches = tuple(candidates)
    if max_tokens is not None and max_tokens > 0:
        from cyt.skills.select import select_skills_within_budget

        candidates = select_skills_within_budget(candidates, max_tokens)

    return candidates, SkillsSearchTrace(
        frontmatter_limit=frontmatter_limit,
        frontmatter_rows=frontmatter_rows,
        pipeline_run=pipeline_run,
        search_item_kind=item_kind,
        search_score_threshold=threshold,
        search_rows=search_rows,
        matches=candidates,
        inject_budget_max=max_tokens,
        pre_budget_matches=pre_budget_matches if not candidates and pre_budget_matches else (),
    )


def search_skills_with_pipeline(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> tuple[list[MatchedSkill], SkillsPipelineRun]:
    """Return matched skills and the configured vs executed search pipeline."""
    matches, trace = search_skills_with_trace(
        query,
        entries,
        config=config,
        max_tokens=max_tokens,
    )
    return matches, trace.pipeline_run


def search_skills(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> list[MatchedSkill]:
    """Return matched skill reconstructions sorted by score (highest first)."""
    matches, _pipeline_run = search_skills_with_pipeline(
        query,
        entries,
        config=config,
        max_tokens=max_tokens,
    )
    return matches

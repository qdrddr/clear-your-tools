"""Rerank selection over decomposed skill nodes (not BM25 chunks)."""

from __future__ import annotations

from typing import Any

from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.config import rerank_score_skills
from cyt.pruners.documents import extract_skill_node_document
from cyt.pruners.remote import RerankPruningSettings
from cyt.pruners.rerank import (
    prune_reranked_skill_items,
    rerank_items,
    rerank_pruning_settings,
)
from cyt.skills.catalog import SkillEntryRef
from cyt.skills.diagnostics import SearchItemRow
from cyt.skills.nodes import build_skill_node_items
from cyt.skills.reconstruct import reconstruct_matches_from_survivor_dicts
from cyt.skills.search import MatchedSkill


def reconstruct_skills_from_reranked_items(
    survivors: list[dict[str, Any]],
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> list[MatchedSkill]:
    """Group surviving node items by doc and rebuild MatchedSkill list."""
    del config
    return reconstruct_matches_from_survivor_dicts(
        survivors,
        entries,
        item_kind="node",
        id_field="node_id",
    )


def rerank_skill_nodes(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
    settings: RerankPruningSettings | None = None,
) -> tuple[list[MatchedSkill], StageTokenUsage]:
    matches, _rows, _threshold, usage = rerank_skill_nodes_with_trace(
        query,
        entries,
        config=config,
        settings=settings,
    )
    return matches, usage


def rerank_skill_nodes_with_trace(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
    settings: RerankPruningSettings | None = None,
) -> tuple[list[MatchedSkill], list[SearchItemRow], float, StageTokenUsage]:
    """Select skill nodes via rerank and return per-node scores."""
    if not query.strip() or not entries:
        return [], [], rerank_score_skills(config), empty_usage()

    items = build_skill_node_items(entries)
    if not items:
        return [], [], rerank_score_skills(config), empty_usage()

    settings = rerank_pruning_settings(config, settings=settings)
    scored, usage = rerank_items(
        query,
        items,
        settings,
        extract_skill_node_document,
        None,
    )
    threshold = rerank_score_skills(config)
    search_rows: list[SearchItemRow] = []
    for item in scored:
        score = float(item.get("score", 0))
        node_id = item.get("node_id")
        if node_id is None:
            continue
        search_rows.append(
            SearchItemRow(
                file_path=str(item.get("file_path", "")),
                doc_id=str(item.get("doc_id", "")),
                item_id=str(node_id),
                item_kind="node",
                score=score,
                passed=score >= threshold,
            ),
        )
    survivors = prune_reranked_skill_items(scored, config=config)
    matches = reconstruct_skills_from_reranked_items(survivors, entries, config=config)
    return matches, search_rows, threshold, usage

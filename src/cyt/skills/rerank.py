"""Rerank selection over decomposed skill nodes (not BM25 chunks)."""

from __future__ import annotations

import logging
from typing import Any

from cyt_indexer import reconstruct_skill_markdown

from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.config import rerank_score_skills, reranker_minimum_tools
from cyt.indexer.build import catalog_tool_count
from cyt.indexer.tokens import count_tokens
from cyt.pruners.documents import (
    extract_json_catalog_document,
    extract_md_catalog_document,
    extract_skill_node_document,
)
from cyt.pruners.remote import RerankPruningSettings
from cyt.pruners.rerank import (
    RERANK_ENUMS,
    prune_reranked_catalog,
    prune_reranked_skill_items,
    rerank_items,
    rerank_pruning_settings,
    rerank_unified_item_lists,
)
from cyt.skills.catalog import SkillEntryRef, load_entry_skills_index
from cyt.skills.diagnostics import SearchItemRow
from cyt.skills.frontmatter import skill_name_from_frontmatter
from cyt.skills.nodes import build_skill_node_items
from cyt.skills.search import MatchedSkill, _frontmatter_by_doc

logger = logging.getLogger(__name__)


def reconstruct_skills_from_reranked_items(
    survivors: list[dict[str, Any]],
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> list[MatchedSkill]:
    """Group surviving node items by doc and rebuild MatchedSkill list."""
    by_doc: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in survivors:
        entry_dir = str(item.get("entry_dir", ""))
        doc_id = str(item.get("doc_id", ""))
        if not entry_dir or not doc_id:
            continue
        by_doc.setdefault((entry_dir, doc_id), []).append(item)

    if not by_doc:
        return []

    frontmatter_by_doc = _frontmatter_by_doc(entries)
    entry_by_key = {(entry.entry_dir, entry.doc_id): entry for entry in entries}
    matched: list[MatchedSkill] = []
    for (entry_dir, doc_id), items in by_doc.items():
        entry = entry_by_key.get((entry_dir, doc_id))
        if entry is None:
            continue
        items.sort(key=lambda row: float(row.get("score", 0)), reverse=True)
        top_score = float(items[0].get("score", 0))
        file_path = str(items[0].get("file_path", ""))
        node_ids = [int(item["node_id"]) for item in items if item.get("node_id") is not None]
        index = load_entry_skills_index(entry)
        node_specs = [str(node_id) for node_id in sorted(set(node_ids))]
        result = reconstruct_skill_markdown(
            index,
            doc_id,
            node_id_specs=node_specs,
        )
        markdown = str(result.get("markdown", "")).strip()
        if not markdown:
            continue
        frontmatter = frontmatter_by_doc.get((entry_dir, doc_id))
        name = skill_name_from_frontmatter(frontmatter)
        token_count = count_tokens(markdown)
        matched.append(
            MatchedSkill(
                doc_id=doc_id,
                file_path=file_path,
                markdown=markdown,
                name=name,
                score=top_score,
                token_count=token_count,
            ),
        )

    return matched


def rerank_skill_nodes(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[MatchedSkill], StageTokenUsage]:
    matches, _rows, _threshold, usage = rerank_skill_nodes_with_trace(
        query,
        entries,
        config=config,
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


def rerank_prune_tools_and_skills(
    data: dict[str, Any],
    query: str,
    skill_entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
    settings: RerankPruningSettings | None = None,
) -> tuple[dict[str, Any], list[MatchedSkill], StageTokenUsage]:
    """Combined tool catalog + skill node rerank in one bulk when possible."""
    settings = rerank_pruning_settings(config, settings=settings)
    targets: list[tuple[list[dict[str, Any]], Any]] = []

    tool_count = catalog_tool_count(data)
    if tool_count >= reranker_minimum_tools(config):
        json_items = data.get("json")
        if isinstance(json_items, list) and json_items:
            targets.append((json_items, extract_json_catalog_document))
        if RERANK_ENUMS:
            md_items = data.get("md")
            if isinstance(md_items, list) and md_items:
                targets.append((md_items, extract_md_catalog_document))

    skill_items = build_skill_node_items(skill_entries)
    if skill_items:
        targets.append((skill_items, extract_skill_node_document))

    if not targets:
        return data, [], empty_usage()

    try:
        usage = rerank_unified_item_lists(query, targets, settings)
    except Exception as exc:
        logger.warning("combined rerank prune failed, falling back to sequential: %s", exc)
        from cyt.pruners.rerank import rerank_catalog_dict

        pruned_data, tool_usage = rerank_catalog_dict(
            data,
            query,
            prune=True,
            merge_pinned=False,
            config=config,
            settings=settings,
        )
        skill_matches, skill_usage = rerank_skill_nodes(
            query,
            skill_entries,
            config=config,
        )
        return pruned_data, skill_matches, tool_usage.merge(skill_usage)

    if tool_count >= reranker_minimum_tools(config):
        data = prune_reranked_catalog(data)

    skill_survivors = prune_reranked_skill_items(skill_items, config=config)
    skill_matches = reconstruct_skills_from_reranked_items(
        skill_survivors,
        skill_entries,
        config=config,
    )
    return data, skill_matches, usage

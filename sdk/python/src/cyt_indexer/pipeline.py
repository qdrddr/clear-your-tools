"""Composite pipeline APIs (Rust-backed single-call orchestration)."""

from __future__ import annotations

from typing import Any

import cyt_indexer._native as _native
from cyt_indexer.build import CatalogIndex
from cyt_indexer.policies import PolicyContext

__all__ = [
    "build_skill_node_catalog",
    "classify_and_count_catalog",
    "coordinate_bm25_prune",
    "prune_catalog_bm25_and_retrieve",
    "recompose_and_retrieve_tools",
    "search_skills_and_select",
]


def prune_catalog_bm25_and_retrieve(
    catalog_data: dict[str, Any],
    build_catalog: dict[str, Any],
    catalog_index: CatalogIndex | dict[str, Any],
    query: str,
    scoring_ctx: PolicyContext,
    output_ctx: PolicyContext,
    *,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Partition, BM25 score, recompose, and retrieve tools in one native call."""
    result = _native.prune_catalog_bm25_and_retrieve(
        catalog_data,
        build_catalog,
        catalog_index,
        query,
        scoring_ctx,
        output_ctx,
        options,
    )
    return dict(result) if isinstance(result, dict) else result


def recompose_and_retrieve_tools(
    data: dict[str, Any],
    build_catalog: dict[str, Any],
    catalog_index: CatalogIndex | dict[str, Any],
    post_rerank: dict[str, Any] | None,
    post_rerank_scored: dict[str, Any] | None,
    pinned: dict[str, Any] | None,
    pipeline: list[str],
    scoring_ctx: PolicyContext,
    output_ctx: PolicyContext,
) -> list[dict[str, Any]]:
    """Recompose pruned catalog survivors and retrieve merged tool schemas in one native call."""
    result = _native.recompose_and_retrieve_tools(
        data,
        build_catalog,
        catalog_index,
        post_rerank,
        post_rerank_scored,
        pinned,
        pipeline,
        scoring_ctx,
        output_ctx,
    )
    if isinstance(result, list):
        return [dict(item) if isinstance(item, dict) else item for item in result]
    return []


def classify_and_count_catalog(
    catalog_data: dict[str, Any],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify optional catalog chunks and optionally count tool JSON tokens."""
    result = _native.classify_and_count_catalog(catalog_data, tools)
    return dict(result) if isinstance(result, dict) else result


def search_skills_and_select(
    entries: list[dict[str, Any]],
    query: str,
    *,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """BM25 skill search with optional frontmatter gate and greedy budget selection."""
    result = _native.search_skills_and_select(entries, query, options)
    return dict(result) if isinstance(result, dict) else result


def build_skill_node_catalog(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Batch-load rerankable node bodies from cached skill entries."""
    result = _native.build_skill_node_catalog(entries)
    if isinstance(result, list):
        return [dict(item) if isinstance(item, dict) else item for item in result]
    return []


def coordinate_bm25_prune(
    skills_entries: list[dict[str, Any]],
    catalog_data: dict[str, Any],
    build_catalog: dict[str, Any],
    catalog_index: CatalogIndex | dict[str, Any],
    query: str,
    scoring_ctx: PolicyContext,
    output_ctx: PolicyContext,
    *,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run skills BM25 search and tool BM25 prune in one native call."""
    result = _native.coordinate_bm25_prune(
        skills_entries,
        catalog_data,
        build_catalog,
        catalog_index,
        query,
        scoring_ctx,
        output_ctx,
        options,
    )
    return dict(result) if isinstance(result, dict) else result

"""Composite pipeline APIs (Rust-backed single-call orchestration)."""

from __future__ import annotations

from typing import Any

import cyt_indexer._native as _native
from cyt_indexer.policies import PolicyContext

__all__ = [
    "build_skill_node_catalog",
    "classify_and_count_catalog",
    "prune_catalog_bm25_and_retrieve",
    "search_skills_and_select",
]


def prune_catalog_bm25_and_retrieve(
    catalog_data: dict[str, Any],
    build_catalog: dict[str, Any],
    catalog_index: dict[str, Any],
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

"""Composite pipeline APIs — re-exports cyt-indexer-sdk."""

from __future__ import annotations

from typing import Any

from cyt_indexer.pipeline import (
    build_skill_node_catalog as _build_skill_node_catalog,
)
from cyt_indexer.pipeline import (
    classify_and_count_catalog as _classify_and_count_catalog,
)
from cyt_indexer.pipeline import (
    coordinate_bm25_prune as _coordinate_bm25_prune,
)
from cyt_indexer.pipeline import (
    prune_catalog_bm25_and_retrieve as _prune_catalog_bm25_and_retrieve,
)
from cyt_indexer.pipeline import (
    search_skills_and_select as _search_skills_and_select,
)

from cyt.indexer.build import CatalogIndex
from cyt.pruners.policies import PolicyContext

__all__ = [
    "build_skill_node_catalog",
    "classify_and_count_catalog",
    "coordinate_bm25_prune",
    "prune_catalog_bm25_and_retrieve",
    "search_skills_and_select",
]


def build_skill_node_catalog(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Batch-load rerankable node bodies (includes cached ``token_count`` when present)."""
    return _build_skill_node_catalog(entries)


def classify_and_count_catalog(
    catalog_data: dict[str, Any],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify optional catalog chunks and optionally count tool JSON tokens."""
    return _classify_and_count_catalog(catalog_data, tools)


def search_skills_and_select(
    entries: list[dict[str, Any]],
    query: str,
    *,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """BM25 skill search with optional frontmatter gate and greedy budget selection."""
    return _search_skills_and_select(entries, query, options=options)


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
    return _prune_catalog_bm25_and_retrieve(
        catalog_data,
        build_catalog,
        catalog_index,
        query,
        scoring_ctx,
        output_ctx,
        options=options,
    )


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
    """Run skills BM25 search and tool BM25 prune in parallel."""
    return _coordinate_bm25_prune(
        skills_entries,
        catalog_data,
        build_catalog,
        catalog_index,
        query,
        scoring_ctx,
        output_ctx,
        options=options,
    )

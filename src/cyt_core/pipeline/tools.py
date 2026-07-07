"""Composite tool pruning pipeline."""

from __future__ import annotations

from typing import Any

from cyt_indexer.pipeline import prune_catalog_bm25_and_retrieve

from cyt_core.types import CatalogSnapshot, PolicyContext

__all__ = ["prune_tools_for_query"]


def prune_tools_for_query(
    catalog: CatalogSnapshot,
    query: str,
    scoring_ctx: PolicyContext,
    output_ctx: PolicyContext,
    *,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run BM25 partition, score, recompose, and retrieve via one native call."""
    return prune_catalog_bm25_and_retrieve(
        catalog.catalog_data,
        catalog.build_catalog,
        catalog.pipeline_catalog_index(),
        query,
        scoring_ctx,
        output_ctx,
        options=options,
    )

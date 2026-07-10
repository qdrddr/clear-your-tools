"""Parallel BM25 coordinator pipeline."""

from __future__ import annotations

from typing import Any

from cyt_core.indexer.pipeline import coordinate_bm25_prune
from cyt_core.types import CatalogSnapshot
from cyt_core.types.policies import PolicyContext

__all__ = ["coordinate_bm25_prune_for_query"]


def coordinate_bm25_prune_for_query(
    *,
    skills_entries: list[dict[str, Any]],
    catalog: CatalogSnapshot,
    query: str,
    scoring_ctx: PolicyContext,
    output_ctx: PolicyContext,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run skills + tools BM25 pruning in one Rust-native parallel call."""
    return coordinate_bm25_prune(
        skills_entries,
        catalog.catalog_data,
        catalog.build_catalog,
        catalog.pipeline_catalog_index(),
        query,
        scoring_ctx,
        output_ctx,
        options=options,
    )

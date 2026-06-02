"""Reconstruct tool schemas from decomposed catalog data (Rust-backed core)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cyt_indexer.retrieve import (
    DECOMPOSED_SCORE,
    ENUM_SCORE,
    DecomposedCatalog,
    load_catalog,
)
from cyt_indexer.retrieve import (
    retrieve_tools as _retrieve_tools,
)

if TYPE_CHECKING:
    from cyt.indexer.build import CatalogIndex

__all__ = [
    "DECOMPOSED_SCORE",
    "ENUM_SCORE",
    "DecomposedCatalog",
    "load_catalog",
    "retrieve_tools",
]


def retrieve_tools(
    data: Any,
    *,
    catalog: DecomposedCatalog | CatalogIndex,
    apply_decomposed_score_filter: bool = True,
    preserve_values: frozenset[str] | None = None,
    system_policy: str | None = None,
    mcp_policy: str | None = None,
) -> list[dict[str, Any]]:
    """
    Reconstruct merged tool schemas from search/rerank/llm output.

    Requires an in-memory ``catalog`` (DecomposedCatalog or CatalogIndex).
    """
    from cyt.pruners import policies as policy_module

    return _retrieve_tools(
        data,
        catalog=catalog,
        apply_decomposed_score_filter=apply_decomposed_score_filter,
        preserve_values=preserve_values,
        system_policy=system_policy,
        mcp_policy=mcp_policy,
        policy_module=policy_module,
    )

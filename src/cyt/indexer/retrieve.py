"""Reconstruct tool schemas from decomposed catalog data (Rust-backed core)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from cyt_indexer.retrieve import DecomposedCatalog, load_catalog
from cyt_indexer.retrieve import (
    retrieve_tools as _retrieve_tools,
)

from cyt.pruners.policies import PolicyContext

if TYPE_CHECKING:
    from cyt.indexer.build import CatalogIndex

__all__ = [
    "DecomposedCatalog",
    "PolicyContext",
    "load_catalog",
    "retrieve_tools",
]


def retrieve_tools(
    data: dict[str, Any],
    *,
    catalog: DecomposedCatalog | CatalogIndex,
    apply_decomposed_score_filter: bool = False,
    preserve_values: frozenset[str] | None = None,
    ctx: PolicyContext | None = None,
    system_policy: str | None = None,
    mcp_policy: str | None = None,
) -> list[dict[str, Any]]:
    """
    Reconstruct merged tool schemas from search/rerank/llm output.

    Pass ``ctx`` for per-request policies (preferred). ``system_policy`` / ``mcp_policy``
    override fields on a config-derived context when ``ctx`` is omitted.
    """
    if ctx is None:
        from cyt.pruners.policies import (
            MCPToolPolicy,
            SystemToolPolicy,
            policy_context_from_config,
        )

        ctx = policy_context_from_config(
            system=cast(SystemToolPolicy | None, system_policy),
            mcp=cast(MCPToolPolicy | None, mcp_policy),
        )

    return _retrieve_tools(
        data,
        catalog=catalog,
        apply_decomposed_score_filter=apply_decomposed_score_filter,
        preserve_values=preserve_values,
        ctx=ctx,
    )

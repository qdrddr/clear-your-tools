"""Reconstruct tool schemas from decomposed catalog data (Rust-backed core)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cyt_indexer._native import (
    DecomposedCatalog,
)
from cyt_indexer._native import (
    chunk_survivor_key as _chunk_survivor_key,
)
from cyt_indexer._native import (
    load_catalog as _load_catalog,
)
from cyt_indexer._native import (
    removed_chunks as _removed_chunks,
)
from cyt_indexer._native import (
    resolve_build_catalog as _resolve_build_catalog,
)
from cyt_indexer._native import (
    retrieve_catalog_tool_count as _retrieve_catalog_tool_count,
)
from cyt_indexer._native import (
    retrieve_core as _retrieve_core,
)
from cyt_indexer._native import (
    retrieve_tools as _retrieve_tools,
)
from cyt_indexer.runtime_defaults import decomposed_score, enum_score

if TYPE_CHECKING:
    from cyt_indexer.build import CatalogIndex
    from cyt_indexer.policies import PolicyContext

DECOMPOSED_SCORE: float = decomposed_score()
ENUM_SCORE: float = enum_score()

__all__ = [
    "DECOMPOSED_SCORE",
    "ENUM_SCORE",
    "DecomposedCatalog",
    "chunk_survivor_key",
    "load_catalog",
    "removed_chunks",
    "resolve_build_catalog",
    "retrieve_catalog_tool_count",
    "retrieve_core",
    "retrieve_tools",
]


def load_catalog(dir_path: str) -> dict[str, list[dict[str, Any]]]:
    """Walk directory and build catalog dict for rerank/llm."""
    return _load_catalog(dir_path)


def chunk_survivor_key(item: dict[str, Any], section: str) -> str | None:
    """Normalized identity for a catalog chunk entry (``json`` or ``md`` section)."""
    return _chunk_survivor_key(item, section)


def removed_chunks(
    full_catalog: dict[str, Any],
    surviving: dict[str, Any],
    *,
    apply_decomposed_score_filter: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Return decomposed chunks in ``full_catalog`` not present in ``surviving``."""
    return _removed_chunks(
        full_catalog,
        surviving,
        apply_decomposed_score_filter,
    )


def resolve_build_catalog(
    catalog: dict[str, Any],
    survivor_data: dict[str, Any],
) -> dict[str, Any]:
    """Resolve build catalog JSON from catalog index or decomposed catalog + survivor data."""
    result = _resolve_build_catalog(catalog, survivor_data)
    return dict(result) if isinstance(result, dict) else result


def retrieve_catalog_tool_count(data: dict[str, Any]) -> int:
    """Count tools in a catalog dict (alias for catalog tool count on survivor data)."""
    return int(_retrieve_catalog_tool_count(data))


def retrieve_core(
    data: dict[str, Any],
    store_json_files: dict[str, Any],
    survivor_json_files: dict[str, Any],
    *,
    apply_decomposed_score_filter: bool = False,
    policy_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Low-level retrieve over survivor json files (same core as C/Go ``cyt_retrieve_core``)."""
    return list(
        _retrieve_core(
            data,
            store_json_files,
            survivor_json_files,
            apply_decomposed_score_filter,
            policy_options,
        ),
    )


def retrieve_tools(
    data: dict[str, Any],
    *,
    catalog: DecomposedCatalog | CatalogIndex,
    apply_decomposed_score_filter: bool = False,
    preserve_values: frozenset[str] | None = None,
    ctx: PolicyContext | None = None,
) -> list[dict[str, Any]]:
    """Reconstruct merged tool schemas from search/rerank/llm output."""
    catalog_arg: Any = catalog
    if hasattr(catalog, "tools") and hasattr(catalog, "files"):
        catalog_arg = {"tools": catalog.tools, "files": catalog.files}
    preserve_list = sorted(preserve_values) if preserve_values else None
    return list(
        _retrieve_tools(
            data,
            catalog_arg,
            apply_decomposed_score_filter,
            preserve_list,
            ctx,
        ),
    )

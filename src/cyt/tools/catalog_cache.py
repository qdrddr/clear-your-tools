"""Rust-backed decomposed tool catalog cache."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyt.cache.policy import cache_policy_for_config
from cyt.config import cache_tools_dir
from cyt.indexer.build import CatalogIndex
from cyt.indexer.cache import ensure_tool_catalog_from_entries
from cyt.pruners.policies import PolicyContext
from cyt_core.types import CatalogSnapshot


@dataclass(frozen=True)
class ToolCatalogCacheResult:
    catalog: dict[str, Any]
    index: CatalogIndex
    cache_status: str
    disk_backed: bool


def ensure_tool_catalog_cached(
    entries: list[dict[str, Any]],
    enums: list[Any],
    config: dict[str, Any],
    *,
    ctx: PolicyContext | None = None,
) -> ToolCatalogCacheResult:
    """Return decomposed catalog and index via Rust cache engine."""
    result = ensure_tool_catalog_from_entries(
        entries,
        enums,
        "",
        str(cache_tools_dir(config)),
        policy=cache_policy_for_config(config),
    )
    catalog = result.get("catalog")
    index_raw = result.get("index")
    if not isinstance(catalog, dict) or not isinstance(index_raw, dict):
        raise ValueError("ensure_tool_catalog_from_entries returned incomplete payload")
    index = CatalogIndex(
        tools=list(index_raw.get("tools", [])),
        files=dict(index_raw.get("files", {})),
    )
    return ToolCatalogCacheResult(
        catalog=catalog,
        index=index,
        cache_status=str(result.get("cache_status", "memory_fallback")),
        disk_backed=bool(result.get("disk_backed")),
    )


def catalog_snapshot_from_cache(result: ToolCatalogCacheResult) -> CatalogSnapshot:
    """Build a pipeline snapshot with a native catalog-index handle."""
    return CatalogSnapshot.from_index(result.catalog, result.catalog, result.index)

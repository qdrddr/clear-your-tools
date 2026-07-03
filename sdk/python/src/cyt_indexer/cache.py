"""Rust-backed disk/memory cache for skills and tool catalogs."""

from __future__ import annotations

from typing import Any

import cyt_indexer._native as _native

CachePolicy = str  # "auto" | "force_memory" | "force_disk"


def tools_catalog_content_hash(tools: list[dict[str, Any]], policy_fingerprint: str) -> str:
    return _native.tools_catalog_content_hash(tools, policy_fingerprint)


def ensure_tool_catalog(
    tools: list[dict[str, Any]],
    policy_fingerprint: str,
    tools_root: str,
    *,
    policy: CachePolicy = "auto",
) -> dict[str, Any]:
    """Ensure decomposed tool catalog on disk; returns catalog dict + cache metadata."""
    result = _native.ensure_tool_catalog(tools, policy_fingerprint, tools_root, policy)
    return dict(result)


def ensure_tool_catalog_from_entries(
    entries: list[dict[str, Any]],
    enums: list[Any],
    policy_fingerprint: str,
    tools_root: str,
    *,
    policy: CachePolicy = "auto",
) -> dict[str, Any]:
    """Ensure decomposed catalog from prepared entries/enums."""
    result = _native.ensure_tool_catalog_from_entries(
        entries,
        enums,
        policy_fingerprint,
        tools_root,
        policy,
    )
    return dict(result)


def ensure_skills_registry(
    source_paths: list[str],
    catalog_root: str,
    pageindex_config: dict[str, Any] | None,
    pipeline: str,
    index_params_hash: str,
    *,
    policy: CachePolicy = "auto",
) -> list[dict[str, Any]]:
    """Ensure page index (+ BM25 chunks when pipeline is bm25) for skill sources."""
    refs = _native.ensure_skills_registry(
        source_paths,
        catalog_root,
        pageindex_config,
        pipeline,
        index_params_hash,
        policy,
    )
    return [dict(ref) for ref in refs]


def configure_memory_cache(config: dict[str, Any]) -> None:
    """Apply in-memory cache tuning (lazy registry, LRU sizes, async disk writes)."""
    _native.configure_memory_cache(config)

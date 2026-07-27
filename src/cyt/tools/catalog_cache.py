"""Rust-backed decomposed tool catalog cache with SWR reads per bulk."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from cyt.cache.policy import cache_policy_for_config
from cyt.config import cache_tools_dir, load_config
from cyt.indexer.build import CatalogIndex, anthropic_tools_to_catalog_entries
from cyt.indexer.cache import ensure_tool_catalog_from_entries
from cyt.pruners.policies import PolicyContext
from cyt_core.types import CatalogSnapshot

logger = logging.getLogger(__name__)

BulkId = str  # "mcpc" | "executor" | "definitions"


@dataclass(frozen=True)
class ToolCatalogCacheResult:
    catalog: dict[str, Any]
    index: CatalogIndex
    cache_status: str
    disk_backed: bool


@dataclass(frozen=True)
class _DecomposedCacheKey:
    bulk_id: str
    tools_dir: str


@dataclass
class _PreparedSelectorCache:
    formatted_chunks: list[str]
    item_metadata_storage: dict[int, Any]
    list_keys: list[str]
    chunk_token_counts: list[int]
    token_rows: list[Any]
    json_count: int
    md_count: int


@dataclass
class _DecomposedCatalogState:
    catalog: dict[str, Any] = field(default_factory=dict)
    index: CatalogIndex = field(default_factory=lambda: CatalogIndex(tools=[], files={}))
    bulk_id: str = ""
    bulk_fingerprint: str = ""
    updated_at: float = 0.0
    cache_status: str = "empty"
    disk_backed: bool = False
    prepared_selector: _PreparedSelectorCache | None = None


_decomposed_lock = threading.Lock()
_decomposed_states: dict[_DecomposedCacheKey, _DecomposedCatalogState] = {}
_refresh_in_progress: set[_DecomposedCacheKey] = set()


def clear_decomposed_catalog_cache() -> None:
    with _decomposed_lock:
        _decomposed_states.clear()
        _refresh_in_progress.clear()


def bulk_content_fingerprint(bulk_id: BulkId, config: dict[str, Any] | None = None) -> str:
    cfg = config or load_config()
    if bulk_id == "definitions":
        from cyt.tools.definitions_catalog import definitions_catalog_fingerprint

        return definitions_catalog_fingerprint(cfg)
    if bulk_id == "executor":
        from cyt.executor.http import executor_catalog_fingerprint

        return executor_catalog_fingerprint(cfg)
    if bulk_id == "mcpc":
        from cyt.mcpc.catalog import mcpc_catalog_fingerprint

        return mcpc_catalog_fingerprint(cfg)
    return ""


def _cache_key(bulk_id: BulkId, config: dict[str, Any]) -> _DecomposedCacheKey:
    return _DecomposedCacheKey(bulk_id=bulk_id, tools_dir=str(cache_tools_dir(config)))


def _get_decomposed_state(cache_key: _DecomposedCacheKey) -> _DecomposedCatalogState:
    with _decomposed_lock:
        state = _decomposed_states.get(cache_key)
        if state is None:
            state = _DecomposedCatalogState(bulk_id=cache_key.bulk_id)
            _decomposed_states[cache_key] = state
        return state


def _count_json_md(data: dict[str, Any]) -> tuple[int, int]:
    json_items = data.get("json")
    md_items = data.get("md")
    json_n = len(json_items) if isinstance(json_items, list) else 0
    md_n = len(md_items) if isinstance(md_items, list) else 0
    return json_n, md_n


def _warm_prepared_selector(state: _DecomposedCatalogState, catalog: dict[str, Any]) -> None:
    try:
        from cyt.pruners.llm import prepare_catalog_selector_chunks

        formatted_chunks, item_metadata_storage, list_keys, chunk_token_counts, token_rows = (
            prepare_catalog_selector_chunks(catalog)
        )
        json_n, md_n = _count_json_md(catalog)
        state.prepared_selector = _PreparedSelectorCache(
            formatted_chunks=formatted_chunks,
            item_metadata_storage=item_metadata_storage,
            list_keys=list_keys,
            chunk_token_counts=chunk_token_counts,
            token_rows=token_rows,
            json_count=json_n,
            md_count=md_n,
        )
    except Exception:
        logger.debug("prepared selector warm skipped bulk=%s", state.bulk_id, exc_info=True)


def get_prepared_selector_chunks(
    data: dict[str, Any],
    *,
    bulk_id: BulkId,
    config: dict[str, Any],
) -> tuple[list[str], dict[int, Any], list[str], list[int], list[Any]] | None:
    """Return warm-prepared selector chunks when *data* matches the full cached catalog."""
    if not bulk_id:
        return None
    json_n, md_n = _count_json_md(data)
    cache_key = _cache_key(bulk_id, config)
    state = _get_decomposed_state(cache_key)
    with _decomposed_lock:
        cached = state.prepared_selector
        if cached is None:
            return None
        if json_n != cached.json_count or md_n != cached.md_count:
            return None
        return (
            list(cached.formatted_chunks),
            dict(cached.item_metadata_storage),
            list(cached.list_keys),
            list(cached.chunk_token_counts),
            list(cached.token_rows),
        )


def _empty_fallback() -> ToolCatalogCacheResult:
    return ToolCatalogCacheResult(
        catalog={},
        index=CatalogIndex(tools=[], files={}),
        cache_status="empty",
        disk_backed=False,
    )


def _snapshot_decomposed(state: _DecomposedCatalogState) -> ToolCatalogCacheResult:
    with _decomposed_lock:
        if not state.catalog:
            return _empty_fallback()
        return ToolCatalogCacheResult(
            catalog=dict(state.catalog),
            index=CatalogIndex(
                tools=list(state.index.tools),
                files=dict(state.index.files),
            ),
            cache_status=state.cache_status,
            disk_backed=state.disk_backed,
        )


def ensure_tool_catalog_cached(
    entries: list[dict[str, Any]],
    enums: list[Any],
    config: dict[str, Any],
    *,
    ctx: PolicyContext | None = None,
    bulk_id: BulkId = "",
) -> ToolCatalogCacheResult:
    """Return decomposed catalog and index via Rust cache engine (blocking builder)."""
    return _build_and_swap(
        bulk_id or "default",
        entries,
        enums,
        config,
        bulk_content_fingerprint(bulk_id or "default", config) if bulk_id else "",
    )


def _build_and_swap(
    bulk_id: BulkId,
    entries: list[dict[str, Any]],
    enums: list[Any],
    config: dict[str, Any],
    fingerprint: str,
) -> ToolCatalogCacheResult:
    result = ensure_tool_catalog_from_entries(
        entries,
        enums,
        bulk_id,
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
    cache_key = _cache_key(bulk_id, config)
    state = _get_decomposed_state(cache_key)
    with _decomposed_lock:
        state.catalog = catalog
        state.index = index
        state.bulk_fingerprint = fingerprint
        state.updated_at = time.monotonic()
        state.cache_status = str(result.get("cache_status", "memory_fallback"))
        state.disk_backed = bool(result.get("disk_backed"))
        state.prepared_selector = None
        _warm_prepared_selector(state, catalog)
    return ToolCatalogCacheResult(
        catalog=catalog,
        index=index,
        cache_status=state.cache_status,
        disk_backed=state.disk_backed,
    )


def get_tool_catalog_cache(
    bulk_id: BulkId,
    entries: list[dict[str, Any]],
    enums: list[Any],
    config: dict[str, Any],
    *,
    blocking: bool = False,
) -> ToolCatalogCacheResult:
    """SWR read of a per-bulk decomposed catalog index."""
    fingerprint = bulk_content_fingerprint(bulk_id, config)
    cache_key = _cache_key(bulk_id, config)
    state = _get_decomposed_state(cache_key)
    with _decomposed_lock:
        fresh = bool(state.catalog) and state.bulk_fingerprint == fingerprint
        has_stale = bool(state.catalog)
    if fresh:
        return _snapshot_decomposed(state)
    if not blocking:
        if has_stale:
            schedule_decomposed_catalog_refresh(bulk_id, entries, enums, config)
            return _snapshot_decomposed(state)
        if entries:
            return _build_and_swap(bulk_id, entries, enums, config, fingerprint)
        schedule_decomposed_catalog_refresh(bulk_id, entries, enums, config)
        return _empty_fallback()
    return _build_and_swap(bulk_id, entries, enums, config, fingerprint)


def schedule_decomposed_catalog_refresh(
    bulk_id: BulkId,
    entries: list[dict[str, Any]],
    enums: list[Any],
    config: dict[str, Any] | None = None,
) -> None:
    cfg = config or load_config()
    cache_key = _cache_key(bulk_id, cfg)
    with _decomposed_lock:
        if cache_key in _refresh_in_progress:
            return
        _refresh_in_progress.add(cache_key)

    def _run() -> None:
        try:
            fingerprint = bulk_content_fingerprint(bulk_id, cfg)
            _build_and_swap(bulk_id, entries, enums, cfg, fingerprint)
        except Exception as exc:
            logger.warning("decomposed catalog refresh failed bulk=%s: %s", bulk_id, exc)
        finally:
            with _decomposed_lock:
                _refresh_in_progress.discard(cache_key)

    threading.Thread(
        target=_run,
        name=f"cyt-decomposed-catalog-{bulk_id}",
        daemon=True,
    ).start()


def schedule_decomposed_catalog_refresh_for_sources(
    config: dict[str, Any],
    sources: tuple[str, ...],
) -> None:
    from cyt.tools.mcpc_prune import mcpc_tools_to_catalog_entries

    for source in sources:
        tools: list[dict[str, Any]] | None = None
        if source == "definitions":
            from cyt.tools.definitions_catalog import get_definitions_catalog

            tools = get_definitions_catalog(config, blocking=False)
        elif source == "executor":
            from cyt.executor.http import get_executor_catalog

            tools = get_executor_catalog(config, allow_prompt=False, blocking=False)
        elif source == "mcpc":
            from cyt.mcpc.catalog import get_mcpc_catalog

            tools = get_mcpc_catalog(config, blocking=False)
        if not tools:
            continue
        if source == "mcpc":
            entries, enums = mcpc_tools_to_catalog_entries(tools)
        else:
            entries, enums = anthropic_tools_to_catalog_entries(tools)
        schedule_decomposed_catalog_refresh(source, entries, enums, config)


def catalog_snapshot_from_cache(result: ToolCatalogCacheResult) -> CatalogSnapshot:
    """Build a pipeline snapshot with a native catalog-index handle."""
    return CatalogSnapshot.from_index(result.catalog, result.catalog, result.index)

"""Master hook tool catalog: concat all configured sources with source tags."""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from cyt.config import (
    load_config,
    resolved_tools_hook_file,
    tools_hook_sources,
    uses_definitions_tool_catalog,
    uses_executor_tool_catalog,
    uses_mcpc_tool_catalog,
)

logger = logging.getLogger(__name__)

CatalogSource = str  # "definitions" | "executor" | "mcpc"


@dataclass(frozen=True)
class _MasterCacheKey:
    sources: tuple[str, ...]
    executor_slug: str
    mcpc_slug: str
    definitions_path: str


@dataclass
class _MasterCatalogState:
    tools: list[dict[str, Any]] = field(default_factory=list)
    source_fingerprints: dict[str, str] = field(default_factory=dict)
    composite_fingerprint: str = ""
    updated_at: float = 0.0


_catalog_lock = threading.Lock()
_catalog_states: dict[_MasterCacheKey, _MasterCatalogState] = {}
_rebuild_in_progress: set[_MasterCacheKey] = set()


def clear_master_catalog_cache() -> None:
    with _catalog_lock:
        _catalog_states.clear()
        _rebuild_in_progress.clear()
    from cyt.tools.master_cache_scheduler import clear_master_cache_schedulers

    clear_master_cache_schedulers()


def build_master_tools(
    ordered_parts: list[tuple[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for source, tools in ordered_parts:
        for tool in tools:
            stamped = copy.deepcopy(tool)
            stamped["cyt_catalog_source"] = source
            merged.append(stamped)
    return merged


def _executor_slug(config: dict[str, Any]) -> str:
    if not uses_executor_tool_catalog(config):
        return ""
    from cyt.executor.http import executor_catalog_slug

    return executor_catalog_slug(config)


def _mcpc_slug(config: dict[str, Any]) -> str:
    if not uses_mcpc_tool_catalog(config):
        return ""
    from cyt.mcpc.catalog import mcpc_catalog_slug

    return mcpc_catalog_slug(config)


def _cache_key_for_config(config: dict[str, Any]) -> _MasterCacheKey:
    cfg = config or load_config()
    return _MasterCacheKey(
        sources=tools_hook_sources(cfg),
        executor_slug=_executor_slug(cfg),
        mcpc_slug=_mcpc_slug(cfg),
        definitions_path=str(resolved_tools_hook_file(cfg).expanduser()),
    )


def _get_state(cache_key: _MasterCacheKey) -> _MasterCatalogState:
    with _catalog_lock:
        state = _catalog_states.get(cache_key)
        if state is None:
            state = _MasterCatalogState()
            _catalog_states[cache_key] = state
        return state


def _current_source_fingerprints(config: dict[str, Any]) -> dict[str, str]:
    fps: dict[str, str] = {}
    if uses_definitions_tool_catalog(config):
        from cyt.tools.definitions_catalog import definitions_catalog_fingerprint

        fps["definitions"] = definitions_catalog_fingerprint(config)
    if uses_executor_tool_catalog(config):
        from cyt.executor.http import executor_catalog_fingerprint

        fps["executor"] = executor_catalog_fingerprint(config)
    if uses_mcpc_tool_catalog(config):
        from cyt.mcpc.catalog import mcpc_catalog_fingerprint

        fps["mcpc"] = mcpc_catalog_fingerprint(config)
    return fps


def _composite_fingerprint(fingerprints: dict[str, str]) -> str:
    parts = [f"{source}:{fingerprints[source]}" for source in sorted(fingerprints)]
    return "|".join(parts)


def _snapshot_master_tools(state: _MasterCatalogState) -> list[dict[str, Any]]:
    with _catalog_lock:
        return copy.deepcopy(state.tools)


def _apply_master_catalog_to_state(
    state: _MasterCatalogState,
    tools: list[dict[str, Any]],
    *,
    source_fingerprints: dict[str, str],
) -> None:
    composite = _composite_fingerprint(source_fingerprints)
    with _catalog_lock:
        state.tools = tools
        state.source_fingerprints = dict(source_fingerprints)
        state.composite_fingerprint = composite
        state.updated_at = time.monotonic()


def _master_is_stale(config: dict[str, Any], state: _MasterCatalogState) -> bool:
    current = _current_source_fingerprints(config)
    with _catalog_lock:
        if not state.tools:
            return True
        if state.source_fingerprints != current:
            return True
    return False


def _load_source_tools(
    config: dict[str, Any],
    source: str,
    *,
    blocking: bool,
) -> list[dict[str, Any]]:
    if source == "definitions":
        from cyt.tools.definitions_catalog import get_definitions_catalog

        return get_definitions_catalog(config, blocking=blocking) or []
    if source == "executor":
        from cyt.executor.http import get_executor_catalog

        return get_executor_catalog(config, allow_prompt=False, blocking=blocking) or []
    if source == "mcpc":
        from cyt.mcpc.catalog import get_mcpc_catalog

        return get_mcpc_catalog(config, blocking=blocking) or []
    return []


def _hydrate_master_from_disk_if_empty(config: dict[str, Any], state: _MasterCatalogState) -> None:
    with _catalog_lock:
        if state.tools:
            return
    if uses_definitions_tool_catalog(config):
        from cyt.tools.definitions_catalog import load_definitions_catalog_from_disk

        load_definitions_catalog_from_disk(config)
    if uses_executor_tool_catalog(config):
        from cyt.executor.http import load_executor_catalog_from_disk

        load_executor_catalog_from_disk(config)
    if uses_mcpc_tool_catalog(config):
        from cyt.mcpc.catalog import load_mcpc_catalog_from_disk

        load_mcpc_catalog_from_disk(config)
    rebuild_master_catalog(config, blocking=False)


def rebuild_master_catalog(config: dict[str, Any] | None = None, *, blocking: bool = False) -> None:
    """Rebuild master snapshot from configured sources."""
    cfg = config or load_config()
    cache_key = _cache_key_for_config(cfg)
    with _catalog_lock:
        if cache_key in _rebuild_in_progress:
            return
        _rebuild_in_progress.add(cache_key)

    try:
        state = _get_state(cache_key)
        prior_tools = _snapshot_master_tools(state)
        with _catalog_lock:
            prior_fingerprints = dict(state.source_fingerprints)
        current_fingerprints = _current_source_fingerprints(cfg)
        ordered_parts: list[tuple[str, list[dict[str, Any]]]] = []
        for source in tools_hook_sources(cfg):
            tools = _load_source_tools(cfg, source, blocking=blocking)
            if tools:
                ordered_parts.append((source, tools))
            elif not blocking:
                prior_for_source = [
                    tool for tool in prior_tools if tool.get("cyt_catalog_source") == source
                ]
                prior_fp = prior_fingerprints.get(source)
                current_fp = current_fingerprints.get(source)
                if prior_for_source and prior_fp == current_fp and prior_fp is not None:
                    ordered_parts.append((source, prior_for_source))
        merged = build_master_tools(ordered_parts)
        fingerprints = _current_source_fingerprints(cfg)
        _apply_master_catalog_to_state(state, merged, source_fingerprints=fingerprints)
        if merged:
            from cyt.tools.catalog_cache import schedule_decomposed_catalog_refresh_for_sources

            schedule_decomposed_catalog_refresh_for_sources(cfg, tools_hook_sources(cfg))
    finally:
        with _catalog_lock:
            _rebuild_in_progress.discard(cache_key)


def get_master_tool_catalog(
    config: dict[str, Any] | None = None,
    *,
    blocking: bool = False,
) -> list[dict[str, Any]] | None:
    """SWR read of the concatenated master catalog."""
    cfg = config or load_config()
    sources = tools_hook_sources(cfg)
    if not sources:
        return None
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)

    snapshot = _snapshot_master_tools(state)
    if not snapshot:
        _hydrate_master_from_disk_if_empty(cfg, state)
        snapshot = _snapshot_master_tools(state)

    if _master_is_stale(cfg, state):
        from cyt.tools.master_cache_scheduler import schedule_master_catalog_refresh

        schedule_master_catalog_refresh(cfg)

    if blocking and not snapshot:
        rebuild_master_catalog(cfg, blocking=True)
        snapshot = _snapshot_master_tools(state)

    return snapshot


def master_catalog_health_snapshot(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        tool_count = len(state.tools)
        age_seconds = time.monotonic() - state.updated_at if state.updated_at else None
        composite = state.composite_fingerprint
        source_fps = dict(state.source_fingerprints)
    payload: dict[str, Any] = {
        "catalog_tool_count": tool_count,
        "configured_sources": list(cache_key.sources),
    }
    if age_seconds is not None:
        payload["catalog_age_seconds"] = round(age_seconds, 1)
    if composite:
        payload["composite_fingerprint_prefix"] = composite[:24]
    if source_fps:
        payload["source_fingerprints"] = {
            source: value[:24] for source, value in source_fps.items() if value
        }
    return payload

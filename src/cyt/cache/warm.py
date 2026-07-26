"""Startup cache warming for skills and BM25 settings."""

from __future__ import annotations

import logging
import threading
from typing import Any

from cyt.cache.memory import configure_memory_cache
from cyt.common.bm25_constants import configure_sdk_bm25_defaults
from cyt.config import (
    cache_enabled,
    load_config,
    skills_enabled,
    tools_hook_sources,
    uses_definitions_tool_catalog,
    uses_executor_tool_catalog,
    uses_mcpc_tool_catalog,
)
from cyt.tools.budget import tools_inject_allowed

logger = logging.getLogger(__name__)


def _bootstrap_source_catalog(cfg: dict[str, Any], source: str) -> list[dict[str, Any]] | None:
    if source == "definitions":
        from cyt.tools.definitions_cache_scheduler import start_definitions_cache_scheduler
        from cyt.tools.definitions_catalog import (
            get_definitions_catalog,
            load_definitions_catalog_from_disk,
        )

        load_definitions_catalog_from_disk(cfg)
        tools = get_definitions_catalog(cfg, blocking=False)
        if not tools:
            tools = get_definitions_catalog(cfg, blocking=True)
        else:
            start_definitions_cache_scheduler(cfg)
        return tools
    if source == "executor":
        from cyt.executor.cache_scheduler import start_executor_cache_scheduler
        from cyt.executor.http import get_executor_catalog, load_executor_catalog_from_disk

        load_executor_catalog_from_disk(cfg)
        tools = get_executor_catalog(cfg, allow_prompt=False, blocking=False)
        if not tools:
            tools = get_executor_catalog(cfg, allow_prompt=False, blocking=True)
        else:
            start_executor_cache_scheduler(cfg, allow_prompt=False)
        return tools
    if source == "mcpc":
        from cyt.mcpc.cache_scheduler import start_mcpc_cache_scheduler
        from cyt.mcpc.catalog import get_mcpc_catalog, load_mcpc_catalog_from_disk
        from cyt.mcpc.readiness import mcpc_hook_catalog_usable

        if not mcpc_hook_catalog_usable(cfg):
            return None
        load_mcpc_catalog_from_disk(cfg)
        tools = get_mcpc_catalog(cfg, blocking=False)
        if not tools:
            tools = get_mcpc_catalog(cfg, blocking=True)
        else:
            start_mcpc_cache_scheduler(cfg)
        return tools
    return None


def _bootstrap_configured_sources(cfg: dict[str, Any], sources: tuple[str, ...]) -> None:
    for source in sources:
        if source == "definitions" and uses_definitions_tool_catalog(cfg):
            _bootstrap_source_catalog(cfg, "definitions")
        elif source == "executor" and uses_executor_tool_catalog(cfg):
            _bootstrap_source_catalog(cfg, "executor")
        elif source == "mcpc" and uses_mcpc_tool_catalog(cfg):
            _bootstrap_source_catalog(cfg, "mcpc")


def _warm_decomposed_bulks(
    cfg: dict[str, Any],
    sources: tuple[str, ...],
    tools: list[dict[str, Any]],
) -> None:
    from cyt.indexer.build import anthropic_tools_to_catalog_entries
    from cyt.tools.catalog_cache import (
        ensure_tool_catalog_cached,
        schedule_decomposed_catalog_refresh,
    )
    from cyt.tools.mcpc_prune import mcpc_tools_to_catalog_entries

    for source in sources:
        bulk_tools = [tool for tool in tools if tool.get("cyt_catalog_source") == source]
        if not bulk_tools:
            continue
        if source == "mcpc":
            entries, enums = mcpc_tools_to_catalog_entries(bulk_tools)
        else:
            entries, enums = anthropic_tools_to_catalog_entries(bulk_tools)
        ensure_tool_catalog_cached(entries, enums, cfg, bulk_id=source)
        schedule_decomposed_catalog_refresh(source, entries, enums, cfg)


def _warm_tools_catalog(cfg: dict[str, Any]) -> None:
    if not tools_inject_allowed(cfg, "hook"):
        return

    from cyt.tools.master_cache_scheduler import start_master_cache_scheduler
    from cyt.tools.master_catalog import get_master_tool_catalog, rebuild_master_catalog

    try:
        sources = tools_hook_sources(cfg)
        _bootstrap_configured_sources(cfg, sources)
        start_master_cache_scheduler(cfg)
        rebuild_master_catalog(cfg, blocking=True)
        tools = get_master_tool_catalog(cfg, blocking=False)
        if not tools:
            return
        _warm_decomposed_bulks(cfg, sources, tools)
        logger.debug("warmed tools catalog cache (%d tools)", len(tools))
    except Exception as exc:
        logger.warning("tools catalog cache warm skipped: %s", exc)


def warm_caches(config: dict[str, Any] | None = None) -> None:
    """Ensure on-disk indexes exist at startup; no-op when cache is disabled."""
    cfg = config or load_config()
    if not cache_enabled(cfg):
        return

    configure_sdk_bm25_defaults(cfg)
    configure_memory_cache(cfg)

    if skills_enabled(cfg):
        from cyt.skills.catalog import build_registry

        try:
            entries = build_registry(cfg)
            logger.debug("warmed skills registry cache (%d entries)", len(entries))
        except Exception as exc:
            logger.warning("skills registry cache warm skipped: %s", exc)

    _warm_tools_catalog(cfg)


def schedule_warm_caches(config: dict[str, Any] | None = None) -> None:
    """Warm caches in a background thread so hook startup stays non-blocking."""
    cfg = config or load_config()
    threading.Thread(
        target=warm_caches,
        args=(cfg,),
        name="cyt-warm-caches",
        daemon=True,
    ).start()

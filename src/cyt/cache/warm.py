"""Startup cache warming for skills and BM25 settings."""

from __future__ import annotations

import logging
from typing import Any

from cyt.cache.memory import configure_memory_cache
from cyt.common.bm25_constants import configure_sdk_bm25_defaults
from cyt.config import cache_enabled, load_config, skills_enabled
from cyt.tools.budget import tools_inject_allowed

logger = logging.getLogger(__name__)


def _warm_tools_catalog(cfg: dict[str, Any]) -> None:
    if not tools_inject_allowed(cfg, "hook"):
        return

    from cyt.indexer.build import anthropic_tools_to_catalog_entries
    from cyt.tools.catalog_cache import ensure_tool_catalog_cached
    from cyt.tools.registry import load_tool_catalog

    try:
        tools = load_tool_catalog(cfg)
        if not tools:
            return
        entries, enums = anthropic_tools_to_catalog_entries(tools)
        ensure_tool_catalog_cached(entries, enums, cfg)
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

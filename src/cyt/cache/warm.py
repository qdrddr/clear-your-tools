"""Startup cache warming for skills and BM25 settings."""

from __future__ import annotations

import logging
from typing import Any

from cyt.common.bm25_constants import configure_sdk_bm25_defaults
from cyt.config import cache_enabled, load_config, skills_enabled

logger = logging.getLogger(__name__)


def warm_caches(config: dict[str, Any] | None = None) -> None:
    """Ensure on-disk indexes exist at startup; no-op when cache is disabled."""
    cfg = config or load_config()
    if not cache_enabled(cfg):
        return

    configure_sdk_bm25_defaults(cfg)

    if not skills_enabled(cfg):
        return

    from cyt.skills.catalog import build_registry

    try:
        entries = build_registry(cfg)
        logger.debug("warmed skills registry cache (%d entries)", len(entries))
    except Exception as exc:
        logger.warning("skills registry cache warm skipped: %s", exc)

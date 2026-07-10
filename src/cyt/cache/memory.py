"""Apply Rust in-memory cache configuration from CYT config."""

from __future__ import annotations

from typing import Any

from cyt.config import cache_memory_settings, load_config


def configure_memory_cache(config: dict[str, Any] | None = None) -> None:
    """Push ``cache.memory`` settings into the cyt-indexer native core."""
    from cyt.indexer.cache import configure_memory_cache as _configure_native

    settings = cache_memory_settings(config or load_config())
    if settings:
        _configure_native(settings)

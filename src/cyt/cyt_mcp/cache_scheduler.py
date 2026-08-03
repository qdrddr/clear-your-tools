"""Background scheduler for cyt-mcp catalog refresh and disk flush."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from cyt.config import load_config, tools_hook_cyt_mcp_cache_settings, uses_cyt_mcp_tool_catalog
from cyt.cyt_mcp.catalog import (
    _cache_key_for_config,
    _fetch_catalog_live,
    _get_state,
    apply_fetched_catalog,
)
from cyt.cyt_mcp.catalog_disk import raw_catalog_content_hash

logger = logging.getLogger(__name__)


@dataclass
class _SchedulerState:
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    tools_in_progress: bool = False
    disk_in_progress: bool = False
    last_tools_refresh_start: float = 0.0
    last_disk_flush_start: float = 0.0
    force_tools_refresh: bool = False


_scheduler_lock = threading.Lock()
_schedulers: dict[str, _SchedulerState] = {}


def clear_cyt_mcp_cache_schedulers() -> None:
    stop_cyt_mcp_cache_scheduler()


def stop_cyt_mcp_cache_scheduler(slug: str | None = None) -> None:
    stopped: list[_SchedulerState] = []
    with _scheduler_lock:
        keys = [slug] if slug is not None else list(_schedulers)
        for key in keys:
            state = _schedulers.pop(key, None)
            if state is None:
                continue
            state.stop_event.set()
            stopped.append(state)
    for state in stopped:
        thread = state.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)


def _get_scheduler_state(slug: str) -> _SchedulerState:
    with _scheduler_lock:
        state = _schedulers.get(slug)
        if state is None:
            state = _SchedulerState()
            _schedulers[slug] = state
        return state


def start_cyt_mcp_cache_scheduler(config: dict[str, Any] | None = None) -> None:
    cfg = config or load_config()
    if not uses_cyt_mcp_tool_catalog(cfg):
        return
    cache_key = _cache_key_for_config(cfg)
    state = _get_scheduler_state(cache_key.slug)
    with _scheduler_lock:
        if state.thread is not None and state.thread.is_alive():
            return
        state.stop_event.clear()
        state.thread = threading.Thread(
            target=_scheduler_loop,
            kwargs={"config": cfg, "slug": cache_key.slug},
            name=f"cyt-mcp-cache-{cache_key.slug}",
            daemon=True,
        )
        state.thread.start()


def schedule_cyt_mcp_catalog_refresh(
    config: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> None:
    cfg = config or load_config()
    if not uses_cyt_mcp_tool_catalog(cfg):
        return
    cache_key = _cache_key_for_config(cfg)
    state = _get_scheduler_state(cache_key.slug)
    if force:
        state.force_tools_refresh = True
    start_cyt_mcp_cache_scheduler(cfg)


def _scheduler_loop(*, config: dict[str, Any], slug: str) -> None:
    state = _get_scheduler_state(slug)
    cache_settings = tools_hook_cyt_mcp_cache_settings(config)
    tools_interval = float(cache_settings.get("tools_refresh_seconds", 120.0))
    disk_interval = float(cache_settings.get("disk_flush_seconds", 900.0))
    cache_key = _cache_key_for_config(config)

    while not state.stop_event.is_set():
        now = time.monotonic()
        if state.force_tools_refresh or now - state.last_tools_refresh_start >= tools_interval:
            state.force_tools_refresh = False
            if not state.tools_in_progress:
                state.tools_in_progress = True
                state.last_tools_refresh_start = now
                try:
                    tools = _fetch_catalog_live(config, cache_key)
                    if tools:
                        apply_fetched_catalog(config, tools)
                except Exception as exc:
                    logger.warning("cyt-mcp scheduler tools refresh failed: %s", exc)
                finally:
                    state.tools_in_progress = False

        if now - state.last_disk_flush_start >= disk_interval:
            if not state.disk_in_progress:
                state.disk_in_progress = True
                state.last_disk_flush_start = now
                try:
                    catalog_state = _get_state(cache_key)
                    if catalog_state.tools:
                        from cyt.cyt_mcp.catalog_disk import write_disk_catalog

                        write_disk_catalog(
                            cache_key.slug,
                            agent=cache_key.agent,
                            tools=catalog_state.tools,
                            content_hash=raw_catalog_content_hash(catalog_state.tools),
                        )
                except Exception as exc:
                    logger.warning("cyt-mcp scheduler disk flush failed: %s", exc)
                finally:
                    state.disk_in_progress = False

        state.stop_event.wait(timeout=1.0)

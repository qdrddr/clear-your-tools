"""Background scheduler for Cloudflare portal catalog, server health, and disk flush."""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cyt.cloudflare.catalog import (
    _cache_key_for_config,
    _cloudflare_runtime_active,
    _CloudflareCacheKey,
    _fetch_catalog_from_network,
    _get_state,
    _resolve_access_credentials,
    _write_catalog_disk,
    apply_fetched_catalog,
)
from cyt.cloudflare.mcp import fetch_portal_list_servers_async
from cyt.cloudflare.runtime import (
    load_config,
    tools_hook_cloudflare_cache_settings,
    tools_hook_cloudflare_url,
)
from cyt.cloudflare.server_health import refresh_server_health, server_fingerprint_for_slug

logger = logging.getLogger(__name__)


@dataclass
class _SchedulerState:
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    health_in_progress: bool = False
    catalog_in_progress: bool = False
    disk_in_progress: bool = False
    last_health_refresh_start: float = 0.0
    last_catalog_refresh_start: float = 0.0
    last_disk_flush_start: float = 0.0
    last_server_fingerprint: str = ""
    force_catalog_refresh: bool = False


_scheduler_lock = threading.Lock()
_schedulers: dict[_CloudflareCacheKey, _SchedulerState] = {}


def stop_cloudflare_cache_scheduler(cache_key: _CloudflareCacheKey | None = None) -> None:
    stopped: list[_SchedulerState] = []
    with _scheduler_lock:
        keys = [cache_key] if cache_key is not None else list(_schedulers)
        for key in keys:
            state = _schedulers.pop(key, None)
            if state is None:
                continue
            state.stop_event.set()
            stopped.append(state)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if all(
            not state.health_in_progress
            and not state.catalog_in_progress
            and not state.disk_in_progress
            for state in stopped
        ):
            break
        time.sleep(0.01)

    for state in stopped:
        thread = state.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))


def clear_cloudflare_cache_schedulers() -> None:
    stop_cloudflare_cache_scheduler()


def _get_scheduler_state(cache_key: _CloudflareCacheKey) -> _SchedulerState:
    with _scheduler_lock:
        state = _schedulers.get(cache_key)
        if state is None:
            state = _SchedulerState()
            _schedulers[cache_key] = state
        return state


def start_cloudflare_cache_scheduler(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = False,
) -> None:
    cfg = config or load_config()
    if not _cloudflare_runtime_active(cfg):
        return
    if not tools_hook_cloudflare_url(cfg):
        return
    cache_key = _cache_key_for_config(cfg)
    state = _get_scheduler_state(cache_key)
    with _scheduler_lock:
        if state.thread is not None and state.thread.is_alive():
            return
        state.stop_event.clear()
        state.thread = threading.Thread(
            target=_scheduler_loop,
            kwargs={"config": cfg, "cache_key": cache_key, "allow_prompt": allow_prompt},
            name=f"cyt-cloudflare-cache-{cache_key.slug}",
            daemon=True,
        )
        state.thread.start()


def schedule_cloudflare_catalog_refresh(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = False,
    force: bool = False,
) -> None:
    cfg = config or load_config()
    if not _cloudflare_runtime_active(cfg):
        return
    if not tools_hook_cloudflare_url(cfg):
        return
    cache_key = _cache_key_for_config(cfg)
    start_cloudflare_cache_scheduler(cfg, allow_prompt=allow_prompt)
    if force:
        state = _get_scheduler_state(cache_key)
        with _scheduler_lock:
            state.force_catalog_refresh = True


def _start_job(
    state: _SchedulerState,
    flag_name: str,
    target: Callable[..., None],
    *,
    config: dict[str, Any],
    cache_key: _CloudflareCacheKey,
) -> None:
    def runner() -> None:
        setattr(state, flag_name, True)
        try:
            target(config=config, cache_key=cache_key)
        finally:
            setattr(state, flag_name, False)

    threading.Thread(target=runner, daemon=True).start()


def _run_health_refresh(*, config: dict[str, Any], cache_key: _CloudflareCacheKey) -> None:
    portal_url = tools_hook_cloudflare_url(config)
    client_id, client_secret = _resolve_access_credentials(config, allow_prompt=False)
    if not portal_url or not client_id or not client_secret:
        return
    try:
        servers = asyncio.run(
            fetch_portal_list_servers_async(
                portal_url=portal_url,
                client_id=client_id,
                client_secret=client_secret,
            ),
        )
        refresh_server_health(slug=cache_key.slug, servers=servers, config=config)
    except Exception as exc:
        logger.warning("cloudflare server health refresh failed: %s", exc)


def _run_catalog_refresh(*, config: dict[str, Any], cache_key: _CloudflareCacheKey) -> None:
    try:
        tools = _fetch_catalog_from_network(config, allow_prompt=False)
    except Exception as exc:
        logger.warning("cloudflare catalog refresh failed: %s", exc)
        return
    apply_fetched_catalog(cache_key, tools, config=config)


def _run_disk_flush(*, config: dict[str, Any], cache_key: _CloudflareCacheKey) -> None:
    state = _get_state(cache_key)
    with _scheduler_lock:
        tools = copy.deepcopy(state.tools)
    if not tools:
        return
    _write_catalog_disk(cache_key, tools=tools, config=config)


def _scheduler_loop(
    *,
    config: dict[str, Any],
    cache_key: _CloudflareCacheKey,
    allow_prompt: bool,
) -> None:
    _ = allow_prompt
    state = _get_scheduler_state(cache_key)
    cache_settings = tools_hook_cloudflare_cache_settings(config)
    health_interval = float(cache_settings.get("server_health_refresh_seconds") or 120.0)
    catalog_interval = float(cache_settings.get("catalog_refresh_seconds") or 120.0)
    disk_interval = float(cache_settings.get("disk_flush_seconds") or 900.0)

    while not state.stop_event.is_set():
        now = time.monotonic()

        if (
            not state.health_in_progress
            and now - state.last_health_refresh_start >= health_interval
        ):
            state.last_health_refresh_start = now
            _start_job(
                state,
                "health_in_progress",
                _run_health_refresh,
                config=config,
                cache_key=cache_key,
            )
            fingerprint = server_fingerprint_for_slug(cache_key.slug)
            if fingerprint and fingerprint != state.last_server_fingerprint:
                state.last_server_fingerprint = fingerprint
                state.force_catalog_refresh = True

        catalog_due = state.force_catalog_refresh or (
            now - state.last_catalog_refresh_start >= catalog_interval
        )
        if catalog_due and not state.catalog_in_progress and not state.health_in_progress:
            state.last_catalog_refresh_start = now
            state.force_catalog_refresh = False
            _start_job(
                state,
                "catalog_in_progress",
                _run_catalog_refresh,
                config=config,
                cache_key=cache_key,
            )

        if not state.disk_in_progress and now - state.last_disk_flush_start >= disk_interval:
            state.last_disk_flush_start = now
            _start_job(
                state,
                "disk_in_progress",
                _run_disk_flush,
                config=config,
                cache_key=cache_key,
            )

        state.stop_event.wait(0.1)

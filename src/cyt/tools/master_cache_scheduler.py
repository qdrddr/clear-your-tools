"""Background scheduler for master hook tool catalog refresh."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from cyt.config import load_config, tools_hook_sources
from cyt.tools.master_catalog import (
    _cache_key_for_config,
    _MasterCacheKey,
    rebuild_master_catalog,
)

logger = logging.getLogger(__name__)

_POLL_SECONDS = 1.0


@dataclass
class _SchedulerState:
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    refresh_in_progress: bool = False
    force_refresh: bool = False
    last_refresh_start: float = 0.0


_scheduler_lock = threading.Lock()
_schedulers: dict[_MasterCacheKey, _SchedulerState] = {}


def clear_master_cache_schedulers() -> None:
    stop_master_cache_scheduler()


def stop_master_cache_scheduler(cache_key: _MasterCacheKey | None = None) -> None:
    stopped: list[_SchedulerState] = []
    with _scheduler_lock:
        keys = [cache_key] if cache_key is not None else list(_schedulers)
        for key in keys:
            state = _schedulers.pop(key, None)
            if state is not None:
                state.stop_event.set()
                stopped.append(state)
    for state in stopped:
        thread = state.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)


def _get_scheduler_state(cache_key: _MasterCacheKey) -> _SchedulerState:
    with _scheduler_lock:
        state = _schedulers.get(cache_key)
        if state is None:
            state = _SchedulerState()
            _schedulers[cache_key] = state
        return state


def start_master_cache_scheduler(config: dict[str, Any] | None = None) -> None:
    cfg = config or load_config()
    if not tools_hook_sources(cfg):
        return
    cache_key = _cache_key_for_config(cfg)
    state = _get_scheduler_state(cache_key)
    with _scheduler_lock:
        if state.thread is not None and state.thread.is_alive():
            return
        state.stop_event.clear()
        state.thread = threading.Thread(
            target=_scheduler_loop,
            kwargs={"config": cfg, "cache_key": cache_key},
            name="cyt-master-catalog-cache",
            daemon=True,
        )
        state.thread.start()


def schedule_master_catalog_refresh(
    config: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> None:
    cfg = config or load_config()
    if not tools_hook_sources(cfg):
        return
    cache_key = _cache_key_for_config(cfg)
    start_master_cache_scheduler(cfg)
    if force:
        state = _get_scheduler_state(cache_key)
        with _scheduler_lock:
            state.force_refresh = True


def _run_refresh(config: dict[str, Any], state: _SchedulerState) -> None:
    try:
        rebuild_master_catalog(config, blocking=False)
    except Exception as exc:
        logger.warning("master catalog refresh failed: %s", exc)
    finally:
        with _scheduler_lock:
            state.force_refresh = False
            state.refresh_in_progress = False


def _scheduler_loop(*, config: dict[str, Any], cache_key: _MasterCacheKey) -> None:
    state = _get_scheduler_state(cache_key)
    while not state.stop_event.is_set():
        now = time.monotonic()
        due = state.force_refresh or (
            state.last_refresh_start == 0.0 or now - state.last_refresh_start >= _POLL_SECONDS
        )
        if due and not state.refresh_in_progress:
            state.last_refresh_start = now
            state.refresh_in_progress = True
            threading.Thread(
                target=_run_refresh,
                args=(config, state),
                daemon=True,
            ).start()
        state.stop_event.wait(timeout=0.1)

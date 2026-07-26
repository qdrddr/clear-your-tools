"""Background scheduler for definitions-file catalog refresh."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from cyt.config import load_config, uses_definitions_tool_catalog
from cyt.tools.definitions_catalog import (
    _cache_key_for_config,
    _DefinitionsCacheKey,
    refresh_definitions_catalog_from_file,
)

logger = logging.getLogger(__name__)

_POLL_SECONDS = 2.0


@dataclass
class _SchedulerState:
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    refresh_in_progress: bool = False
    last_refresh_start: float = 0.0


_scheduler_lock = threading.Lock()
_schedulers: dict[_DefinitionsCacheKey, _SchedulerState] = {}


def clear_definitions_cache_schedulers() -> None:
    stop_definitions_cache_scheduler()


def stop_definitions_cache_scheduler(cache_key: _DefinitionsCacheKey | None = None) -> None:
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


def _get_scheduler_state(cache_key: _DefinitionsCacheKey) -> _SchedulerState:
    with _scheduler_lock:
        state = _schedulers.get(cache_key)
        if state is None:
            state = _SchedulerState()
            _schedulers[cache_key] = state
        return state


def start_definitions_cache_scheduler(config: dict[str, Any] | None = None) -> None:
    cfg = config or load_config()
    if not uses_definitions_tool_catalog(cfg):
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
            name=f"cyt-definitions-cache-{cache_key.path}",
            daemon=True,
        )
        state.thread.start()


def _scheduler_loop(*, config: dict[str, Any], cache_key: _DefinitionsCacheKey) -> None:
    state = _get_scheduler_state(cache_key)
    while not state.stop_event.is_set():
        now = time.monotonic()
        if not state.refresh_in_progress and now - state.last_refresh_start >= _POLL_SECONDS:
            state.last_refresh_start = now
            state.refresh_in_progress = True

            def _run() -> None:
                try:
                    refresh_definitions_catalog_from_file(config)
                except Exception as exc:
                    logger.warning(
                        "definitions catalog refresh failed path=%s: %s",
                        cache_key.path,
                        exc,
                    )
                finally:
                    state.refresh_in_progress = False

            threading.Thread(target=_run, daemon=True).start()
        state.stop_event.wait(timeout=0.25)

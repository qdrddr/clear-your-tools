"""Background scheduler for MCPC catalog, session health, and disk flush."""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cyt.mcpc.catalog import (
    _cache_key_for_config,
    _fetch_catalog_from_cli,
    _get_state,
    _mcpc_runtime_active,
    _McpcCacheKey,
    _write_catalog_disk,
    apply_fetched_catalog,
)
from cyt.mcpc.runtime import (
    load_config,
    tools_hook_mcpc_cache_settings,
)
from cyt.mcpc.session_health import (
    refresh_session_health,
    session_fingerprint_for_slug,
)

logger = logging.getLogger(__name__)


@dataclass
class _SchedulerState:
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    session_in_progress: bool = False
    tools_in_progress: bool = False
    skills_in_progress: bool = False
    disk_in_progress: bool = False
    last_session_refresh_start: float = 0.0
    last_tools_refresh_start: float = 0.0
    last_skills_refresh_start: float = 0.0
    last_disk_flush_start: float = 0.0
    last_session_fingerprint: str = ""
    force_tools_refresh: bool = False


_scheduler_lock = threading.Lock()
_schedulers: dict[_McpcCacheKey, _SchedulerState] = {}


def stop_mcpc_cache_scheduler(cache_key: _McpcCacheKey | None = None) -> None:
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
            not state.session_in_progress
            and not state.tools_in_progress
            and not state.skills_in_progress
            and not state.disk_in_progress
            for state in stopped
        ):
            break
        time.sleep(0.01)

    for state in stopped:
        thread = state.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))


def clear_mcpc_cache_schedulers() -> None:
    stop_mcpc_cache_scheduler()


def _get_scheduler_state(cache_key: _McpcCacheKey) -> _SchedulerState:
    with _scheduler_lock:
        state = _schedulers.get(cache_key)
        if state is None:
            state = _SchedulerState()
            _schedulers[cache_key] = state
        return state


def start_mcpc_cache_scheduler(config: dict[str, Any] | None = None) -> None:
    cfg = config or load_config()
    if not _mcpc_runtime_active(cfg):
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
            name=f"cyt-mcpc-cache-{cache_key.slug}",
            daemon=True,
        )
        state.thread.start()


def schedule_mcpc_catalog_refresh(
    config: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> None:
    cfg = config or load_config()
    if not _mcpc_runtime_active(cfg):
        return
    cache_key = _cache_key_for_config(cfg)
    start_mcpc_cache_scheduler(cfg)
    if force:
        state = _get_scheduler_state(cache_key)
        with _scheduler_lock:
            state.force_tools_refresh = True


def _scheduler_loop(*, config: dict[str, Any], cache_key: _McpcCacheKey) -> None:
    state = _get_scheduler_state(cache_key)
    cache_settings = tools_hook_mcpc_cache_settings(config)
    session_interval = float(cache_settings.get("session_refresh_seconds") or 1.0)
    tools_interval = float(cache_settings.get("tools_refresh_seconds") or 120.0)
    skills_interval = float(cache_settings.get("skills_refresh_seconds") or 120.0)
    disk_interval = float(cache_settings.get("disk_flush_seconds") or 900.0)

    while not state.stop_event.is_set():
        now = time.monotonic()

        if (
            not state.session_in_progress
            and now - state.last_session_refresh_start >= session_interval
        ):
            state.last_session_refresh_start = now
            _start_job(
                state,
                "session_in_progress",
                _run_session_refresh,
                config=config,
                cache_key=cache_key,
            )

        fingerprint = session_fingerprint_for_slug(cache_key.slug)
        tools_due = (
            state.force_tools_refresh
            or (fingerprint and fingerprint != state.last_session_fingerprint)
            or (
                state.last_tools_refresh_start == 0.0
                or now - state.last_tools_refresh_start >= tools_interval
            )
        )
        if tools_due and not state.tools_in_progress and not state.session_in_progress:
            state.last_tools_refresh_start = now
            _start_job(
                state,
                "tools_in_progress",
                _run_tools_refresh,
                config=config,
                cache_key=cache_key,
            )

        skills_due = (
            state.last_skills_refresh_start == 0.0
            or now - state.last_skills_refresh_start >= skills_interval
        )
        if skills_due and not state.skills_in_progress and not state.session_in_progress:
            state.last_skills_refresh_start = now
            _start_job(
                state,
                "skills_in_progress",
                _run_skills_refresh,
                config=config,
                cache_key=cache_key,
            )

        if not state.disk_in_progress and (
            state.last_disk_flush_start == 0.0 or now - state.last_disk_flush_start >= disk_interval
        ):
            state.last_disk_flush_start = now
            _start_job(
                state,
                "disk_in_progress",
                _run_disk_flush,
                config=config,
                cache_key=cache_key,
            )

        state.stop_event.wait(timeout=0.1)


def _start_job(
    state: _SchedulerState,
    flag_name: str,
    target: Callable[..., None],
    **kwargs: object,
) -> None:
    setattr(state, flag_name, True)

    def _wrapper() -> None:
        try:
            target(**kwargs)
        finally:
            setattr(state, flag_name, False)

    threading.Thread(target=_wrapper, daemon=True).start()


def _run_session_refresh(*, config: dict[str, Any], cache_key: _McpcCacheKey) -> None:
    state = _get_scheduler_state(cache_key)
    try:
        refresh_session_health(
            executable=cache_key.executable,
            slug=cache_key.slug,
            config=config,
        )
        fingerprint = session_fingerprint_for_slug(cache_key.slug)
        with _scheduler_lock:
            if fingerprint and fingerprint != state.last_session_fingerprint:
                state.force_tools_refresh = True
            state.last_session_fingerprint = fingerprint
    except Exception as exc:
        logger.warning("mcpc session refresh failed slug=%s: %s", cache_key.slug, exc)


def _run_tools_refresh(*, config: dict[str, Any], cache_key: _McpcCacheKey) -> None:
    state = _get_scheduler_state(cache_key)
    try:
        tools, sessions = _fetch_catalog_from_cli(
            cache_key.executable,
            cache_key.slug,
            config=config,
        )
        apply_fetched_catalog(cache_key, tools, sessions, config=config)
        with _scheduler_lock:
            state.force_tools_refresh = False
            state.last_session_fingerprint = session_fingerprint_for_slug(cache_key.slug)
    except Exception as exc:
        logger.warning("mcpc tools refresh failed slug=%s: %s", cache_key.slug, exc)


def _run_skills_refresh(*, config: dict[str, Any], cache_key: _McpcCacheKey) -> None:
    from cyt.mcpc.skills_cache import refresh_mcpc_skills_snapshot

    try:
        refresh_mcpc_skills_snapshot(config)
    except Exception as exc:
        logger.warning("mcpc skills refresh failed slug=%s: %s", cache_key.slug, exc)


def _run_disk_flush(*, config: dict[str, Any], cache_key: _McpcCacheKey) -> None:
    catalog_state = _get_state(cache_key)
    if not catalog_state.tools:
        return
    _write_catalog_disk(
        cache_key,
        tools=copy.deepcopy(catalog_state.tools),
        sessions=copy.deepcopy(catalog_state.sessions),
        config=config,
    )

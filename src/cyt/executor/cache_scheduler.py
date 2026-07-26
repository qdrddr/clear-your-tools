"""Background scheduler for executor catalog, health, and disk flush."""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from cyt.executor.connection_health import (
    ConnectionHealthSnapshot,
    ConnectionKey,
    connection_fingerprint_for_slug,
    debug_disk_enabled,
    filter_summaries_for_schema_fetch,
    health_snapshot_to_disk,
    refresh_connection_health_async,
    snapshot_health_for_catalog,
)
from cyt.executor.http import (
    _apply_catalog_to_state,
    _cache_key_for_config,
    _executor_runtime_active,
    _ExecutorCacheKey,
    _fetch_list_async,
    _fetch_schemas_async,
    _get_state,
    _resolve_executor_token,
    _summaries_to_stub_tools,
    _write_catalog_disk,
    evict_schemas_for_connections,
    merge_list_stubs_into_catalog,
)
from cyt.executor.runtime import (
    load_config,
    tools_hook_executor_cache_settings,
    tools_hook_executor_url,
)

logger = logging.getLogger(__name__)


@dataclass
class _SchedulerState:
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    health_in_progress: bool = False
    list_in_progress: bool = False
    schema_in_progress: bool = False
    disk_in_progress: bool = False
    last_health_cycle_start: float = 0.0
    last_schema_refresh_start: float = 0.0
    last_disk_flush_start: float = 0.0
    last_connection_fingerprint: str = ""
    force_list_refresh: bool = False
    force_schema_refresh: bool = False
    pending_schema_keys: set[ConnectionKey] = field(default_factory=set)


_scheduler_lock = threading.Lock()
_schedulers: dict[_ExecutorCacheKey, _SchedulerState] = {}


def stop_executor_cache_scheduler(
    cache_key: _ExecutorCacheKey | None = None,
) -> None:
    """Stop one scheduler or all schedulers (for tests)."""
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
            and not state.list_in_progress
            and not state.schema_in_progress
            and not state.disk_in_progress
            for state in stopped
        ):
            break
        time.sleep(0.01)

    for state in stopped:
        thread = state.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))


def clear_executor_cache_schedulers() -> None:
    stop_executor_cache_scheduler()


def _get_scheduler_state(cache_key: _ExecutorCacheKey) -> _SchedulerState:
    with _scheduler_lock:
        state = _schedulers.get(cache_key)
        if state is None:
            state = _SchedulerState()
            _schedulers[cache_key] = state
        return state


def start_executor_cache_scheduler(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = False,
) -> None:
    """Start the background scheduler when executor hook catalog is active."""
    cfg = config or load_config()
    if not _executor_runtime_active(cfg):
        return
    if not tools_hook_executor_url(cfg):
        return

    token = _resolve_executor_token(cfg, allow_prompt=allow_prompt)
    cache_key = _cache_key_for_config(cfg, token)
    state = _get_scheduler_state(cache_key)

    with _scheduler_lock:
        if state.thread is not None and state.thread.is_alive():
            return
        state.stop_event.clear()
        state.thread = threading.Thread(
            target=_scheduler_loop,
            kwargs={"config": cfg, "token": token, "cache_key": cache_key},
            name=f"cyt-executor-cache-{cache_key.slug}",
            daemon=True,
        )
        state.thread.start()


def schedule_executor_catalog_refresh(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = False,
    force: bool = False,
) -> None:
    """Ensure scheduler is running; optionally force list + schema refresh."""
    cfg = config or load_config()
    if not _executor_runtime_active(cfg):
        return
    if not tools_hook_executor_url(cfg):
        return

    token = _resolve_executor_token(cfg, allow_prompt=allow_prompt)
    cache_key = _cache_key_for_config(cfg, token)
    start_executor_cache_scheduler(cfg, allow_prompt=allow_prompt)
    if force:
        state = _get_scheduler_state(cache_key)
        with _scheduler_lock:
            state.force_list_refresh = True
            state.force_schema_refresh = True


def _scheduler_loop(
    *,
    config: dict[str, Any],
    token: str | None,
    cache_key: _ExecutorCacheKey,
) -> None:
    state = _get_scheduler_state(cache_key)
    cache_settings = tools_hook_executor_cache_settings(config)
    health_interval = float(cache_settings.get("health_refresh_seconds") or 1.0)
    schema_interval = float(cache_settings.get("catalog_schema_refresh_seconds") or 120.0)
    disk_interval = float(cache_settings.get("disk_flush_seconds") or 900.0)

    while not state.stop_event.is_set():
        now = time.monotonic()

        if not state.health_in_progress and now - state.last_health_cycle_start >= health_interval:
            state.last_health_cycle_start = now
            _start_job(
                state,
                "health_in_progress",
                _run_health_refresh,
                config=config,
                token=token,
                cache_key=cache_key,
            )

        fingerprint = connection_fingerprint_for_slug(cache_key.slug)
        list_due = state.force_list_refresh or (
            fingerprint and fingerprint != state.last_connection_fingerprint
        )
        if list_due and not state.list_in_progress:
            _start_job(
                state,
                "list_in_progress",
                _run_catalog_list_refresh,
                config=config,
                token=token,
                cache_key=cache_key,
            )

        schema_due = (
            state.force_schema_refresh
            or state.pending_schema_keys
            or (
                state.last_schema_refresh_start == 0.0
                or now - state.last_schema_refresh_start >= schema_interval
            )
        )
        if schema_due and not state.schema_in_progress and not state.list_in_progress:
            state.last_schema_refresh_start = now
            _start_job(
                state,
                "schema_in_progress",
                _run_catalog_schema_refresh,
                config=config,
                token=token,
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


def _run_health_refresh(
    *,
    config: dict[str, Any],
    token: str | None,
    cache_key: _ExecutorCacheKey,
) -> None:
    state = _get_scheduler_state(cache_key)
    try:
        _health_snapshot, delta = asyncio.run(
            refresh_connection_health_async(
                base_url=cache_key.base_url,
                token=token,
                slug=cache_key.slug,
                config=config,
            ),
        )
        fingerprint = connection_fingerprint_for_slug(cache_key.slug)
        with _scheduler_lock:
            if fingerprint and fingerprint != state.last_connection_fingerprint:
                state.force_list_refresh = True
            if delta is not None:
                if delta.newly_ineligible:
                    catalog_state = _get_state(cache_key)
                    evict_schemas_for_connections(
                        catalog_state,
                        delta.newly_ineligible,
                    )
                if delta.newly_eligible:
                    state.pending_schema_keys.update(delta.newly_eligible)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("executor health refresh failed slug=%s: %s", cache_key.slug, exc)


def _run_catalog_list_refresh(
    *,
    config: dict[str, Any],
    token: str | None,
    cache_key: _ExecutorCacheKey,
) -> None:
    state = _get_scheduler_state(cache_key)
    try:
        summaries, executor_mcp = asyncio.run(
            _fetch_list_and_mcp_async(
                base_url=cache_key.base_url,
                token=token,
            ),
        )
        catalog_state = _get_state(cache_key)
        stubs = _summaries_to_stub_tools(summaries)
        merge_list_stubs_into_catalog(catalog_state, stubs)
        _apply_catalog_to_state(
            catalog_state,
            copy.deepcopy(catalog_state.tools),
            executor_mcp=executor_mcp,
            config=config,
        )
        with _scheduler_lock:
            state.last_connection_fingerprint = connection_fingerprint_for_slug(cache_key.slug)
            state.force_list_refresh = False
            state.force_schema_refresh = True
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("executor catalog list refresh failed slug=%s: %s", cache_key.slug, exc)


async def _fetch_list_and_mcp_async(
    *,
    base_url: str,
    token: str | None,
) -> tuple[list[tuple[str, str | None, dict[str, Any]]], dict[str, Any] | None]:
    from cyt.executor.mcp import fetch_executor_mcp_cache_async

    list_task = asyncio.create_task(_fetch_list_async(base_url=base_url, token=token))
    mcp_task = asyncio.create_task(
        fetch_executor_mcp_cache_async(base_url=base_url, token=token),
    )
    summaries, mcp_outcome = await asyncio.gather(list_task, mcp_task, return_exceptions=True)
    if isinstance(summaries, BaseException):
        raise summaries
    executor_mcp: dict[str, Any] | None
    if isinstance(mcp_outcome, BaseException):
        logger.warning("executor MCP cache fetch failed: %s", mcp_outcome)
        executor_mcp = None
    else:
        executor_mcp = mcp_outcome
    return summaries, executor_mcp


def _run_catalog_schema_refresh(
    *,
    config: dict[str, Any],
    token: str | None,
    cache_key: _ExecutorCacheKey,
) -> None:
    state = _get_scheduler_state(cache_key)
    catalog_state = _get_state(cache_key)
    try:
        summaries = _summaries_from_catalog(catalog_state.tools)
        eligible = filter_summaries_for_schema_fetch(
            summaries,
            cache_key.slug,
            config=config,
        )
        with _scheduler_lock:
            pending = set(state.pending_schema_keys)
            state.pending_schema_keys.clear()
            force_all = state.force_schema_refresh
            state.force_schema_refresh = False
        if pending and not force_all:
            eligible = [
                summary for summary in eligible if _summary_connection_key(summary) in pending
            ]
        if not eligible:
            return
        schema_tools = asyncio.run(
            _fetch_schemas_async(
                base_url=cache_key.base_url,
                token=token,
                summaries=eligible,
            ),
        )
        merge_list_stubs_into_catalog(catalog_state, schema_tools)
        _apply_catalog_to_state(
            catalog_state,
            copy.deepcopy(catalog_state.tools),
            config=config,
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("executor catalog schema refresh failed slug=%s: %s", cache_key.slug, exc)


def _summary_connection_key(
    summary: tuple[str, str | None, dict[str, Any]],
) -> ConnectionKey | None:
    from cyt.executor.connection_health import connection_key_from_tool

    return connection_key_from_tool(summary[2])


def _summaries_from_catalog(
    tools: list[dict[str, Any]],
) -> list[tuple[str, str | None, dict[str, Any]]]:
    summaries: list[tuple[str, str | None, dict[str, Any]]] = []
    for tool in tools:
        address = str(tool.get("name") or "").strip()
        if not address:
            continue
        description = tool.get("description")
        desc_text = str(description) if description is not None else None
        metadata: dict[str, Any] = {}
        for key in ("owner", "integration", "connection", "static", "tool_name"):
            if key in tool:
                metadata[key] = tool[key]
        summaries.append((address, desc_text, metadata))
    return summaries


def _run_disk_flush(
    *,
    config: dict[str, Any],
    cache_key: _ExecutorCacheKey,
) -> None:
    catalog_state = _get_state(cache_key)
    if not catalog_state.tools:
        return
    connections_health = None
    if debug_disk_enabled():
        snapshot_fields = health_snapshot_to_disk(
            _health_snapshot_for_slug(cache_key.slug),
            slug=cache_key.slug,
            config=config,
        )
        connections_health = snapshot_fields
    _write_catalog_disk(
        cache_key,
        tools=copy.deepcopy(catalog_state.tools),
        executor_mcp=copy.deepcopy(catalog_state.executor_mcp),
        connections_health=connections_health,
        config=config,
    )


def _health_snapshot_for_slug(slug: str) -> ConnectionHealthSnapshot:
    snapshot = snapshot_health_for_catalog(slug)
    if snapshot is not None:
        return snapshot
    return ConnectionHealthSnapshot()

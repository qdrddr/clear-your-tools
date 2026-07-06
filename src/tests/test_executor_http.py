"""Tests for executor HTTP tool source."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Coroutine, Iterable, Mapping
from typing import Any
from unittest.mock import patch

import httpx

from cyt.tools.sources.executor_http import (
    _ExecutorCacheKey,
    clear_executor_catalog_cache,
    fetch_executor_tools,
    load_executor_tools,
    schedule_executor_catalog_refresh,
)

_CONFIG = {
    "pruning": {
        "tools": {
            "hook": {
                "executor_url": "http://localhost:4789",
                "executor_token_var": "EXECUTOR_TOKEN",
            },
        },
    },
}


def setup_function() -> None:
    clear_executor_catalog_cache()


def test_fetch_executor_tools_blocking_normalizes_list_and_schema() -> None:
    expected = [
        {
            "name": "tools.demo.org.default.search",
            "description": "Search files",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    ]

    with (
        patch(
            "cyt.tools.sources.executor_http.resolve_credential",
            return_value=("secret-token", "keyring"),
        ),
        patch(
            "cyt.tools.sources.executor_http.asyncio.run",
            side_effect=lambda coro: (
                coro.close(),
                expected,
            )[1],
        ) as run_mock,
    ):
        tools = fetch_executor_tools(_CONFIG, blocking=True)

    assert tools == expected
    run_mock.assert_called_once()


def test_fetch_executor_tools_blocking_returns_stale_on_http_error() -> None:
    stale = [{"name": "tools.cached.tool", "description": "Cached"}]

    def _raise_http_error(coro: Coroutine[Any, Any, Any]) -> list[dict[str, object]]:
        coro.close()
        raise httpx.HTTPError("boom")

    with (
        patch(
            "cyt.tools.sources.executor_http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.tools.sources.executor_http.asyncio.run",
            side_effect=_raise_http_error,
        ),
    ):
        assert fetch_executor_tools(_CONFIG, blocking=True) == []

    with (
        patch(
            "cyt.tools.sources.executor_http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.tools.sources.executor_http.asyncio.run",
            side_effect=_raise_http_error,
        ),
        patch(
            "cyt.tools.sources.executor_http._snapshot_tools",
            return_value=stale,
        ),
    ):
        assert fetch_executor_tools(_CONFIG, blocking=True) == stale


def test_load_executor_tools_returns_stale_while_refresh_in_progress() -> None:
    stale = [{"name": "tools.stale.tool", "description": "Old catalog"}]
    refreshed = [{"name": "tools.new.tool", "description": "New catalog"}]
    refresh_started = threading.Event()
    refresh_release = threading.Event()
    refresh_threads: list[threading.Thread] = []

    class _TrackingThread(threading.Thread):
        def __init__(
            self,
            group: None = None,
            target: Callable[..., object] | None = None,
            name: str | None = None,
            args: Iterable[object] = (),
            kwargs: Mapping[str, object] | None = None,
            *,
            daemon: bool | None = None,
        ) -> None:
            super().__init__(
                group=group,
                target=target,
                name=name,
                args=args,
                kwargs=kwargs,
                daemon=daemon,
            )
            refresh_threads.append(self)

    def _slow_refresh(
        *,
        config: dict[str, object],
        token: str | None,
        cache_key: _ExecutorCacheKey,
    ) -> None:
        refresh_started.set()
        refresh_release.wait(timeout=2.0)
        from cyt.tools.sources import executor_http

        with executor_http._catalog_lock:
            executor_state = executor_http._catalog_states[
                executor_http._cache_key_for_config(_CONFIG, "token")
            ]
            executor_state.tools = refreshed
            executor_state.updated_at = time.monotonic()
            executor_state.refresh_in_progress = False

    with (
        patch(
            "cyt.tools.sources.executor_http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.tools.sources.executor_http._snapshot_tools",
            return_value=stale,
        ),
        patch(
            "cyt.tools.sources.executor_http._run_background_refresh",
            side_effect=_slow_refresh,
        ),
        patch(
            "cyt.tools.sources.executor_http.threading.Thread",
            _TrackingThread,
        ),
    ):
        tools = load_executor_tools(_CONFIG, allow_prompt=False, blocking=False)

    assert tools == stale
    assert refresh_started.wait(timeout=2.0)

    with (
        patch(
            "cyt.tools.sources.executor_http._snapshot_tools",
            return_value=stale,
        ),
        patch(
            "cyt.tools.sources.executor_http.schedule_executor_catalog_refresh",
        ),
    ):
        still_stale = load_executor_tools(_CONFIG, allow_prompt=False, blocking=False)

    assert still_stale == stale

    refresh_release.set()
    for thread in refresh_threads:
        thread.join(timeout=2.0)

    with (
        patch(
            "cyt.tools.sources.executor_http._snapshot_tools",
            return_value=refreshed,
        ),
        patch(
            "cyt.tools.sources.executor_http.schedule_executor_catalog_refresh",
        ),
    ):
        updated = load_executor_tools(_CONFIG, allow_prompt=False, blocking=False)

    assert updated == refreshed


def test_schedule_executor_catalog_refresh_skips_when_not_stale() -> None:
    now = time.monotonic()
    with (
        patch(
            "cyt.tools.sources.executor_http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.tools.sources.executor_http.time.monotonic",
            return_value=now,
        ),
        patch(
            "cyt.tools.sources.executor_http._get_state",
        ) as get_state_mock,
        patch(
            "cyt.tools.sources.executor_http.threading.Thread",
        ) as thread_mock,
    ):
        state = get_state_mock.return_value
        state.tools = [{"name": "tools.cached"}]
        state.updated_at = now
        state.refresh_in_progress = False

        schedule_executor_catalog_refresh(_CONFIG, force=False)

    thread_mock.assert_not_called()

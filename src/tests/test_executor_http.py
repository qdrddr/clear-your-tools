"""Tests for executor HTTP tool source."""

from __future__ import annotations

import json
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from cyt.executor.catalog_disk import (
    normalize_executor_url_slug,
    raw_catalog_content_hash,
    raw_executor_mcp_content_hash,
    read_disk_catalog,
    write_disk_catalog,
)
from cyt.executor.http import (
    _cache_key_for_config,
    clear_executor_catalog_cache,
    fetch_executor_tools,
    get_executor_catalog,
    load_executor_tools,
    schedule_executor_catalog_refresh,
)
from cyt.executor.mcp import (
    _parse_jsonrpc_body,
    _skill_text_from_call_result,
)

_CONFIG = {
    "pruning": {
        "inject_via": "hook",
        "tools": {
            "hook": {
                "tools_from": "executor",
                "executor_url": "http://localhost:4789",
                "executor_token_var": "EXECUTOR_TOKEN",
            },
        },
    },
}


def setup_function() -> None:
    clear_executor_catalog_cache()


def test_normalize_executor_url_slug_examples() -> None:
    assert normalize_executor_url_slug("http://localhost:4789") == "http___localhost_4789"
    assert (
        normalize_executor_url_slug("https://api.example.com:8080/")
        == "https___api.example.com_8080"
    )
    assert (
        normalize_executor_url_slug("http://localhost:4789", token_var="EXECUTOR_TOKEN")
        == "http___localhost_4789__EXECUTOR_TOKEN"
    )


def test_raw_catalog_content_hash_is_order_independent() -> None:
    tools_a = [
        {"name": "tools.b", "description": "B", "input_schema": {"type": "object"}},
        {"name": "tools.a", "description": "A", "input_schema": {"type": "object"}},
    ]
    tools_b = list(reversed(tools_a))
    assert raw_catalog_content_hash(tools_a) == raw_catalog_content_hash(tools_b)


def test_write_disk_catalog_skips_when_hash_unchanged(tmp_path: Path) -> None:
    slug = "http___localhost_4789"
    tools = [{"name": "tools.demo", "description": "Demo", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    cache_dir = tmp_path / "executor-catalog"
    path = cache_dir / f"{slug}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "executor_url": "http://localhost:4789",
                "catalog_content_hash": content_hash,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "tool_count": 1,
                "tools": tools,
            },
        ),
        encoding="utf-8",
    )
    mtime_before = path.stat().st_mtime_ns

    with patch(
        "cyt.executor.catalog_disk.executor_catalog_cache_dir",
        return_value=cache_dir,
    ):
        action = write_disk_catalog(
            slug,
            executor_url="http://localhost:4789",
            tools=tools,
            content_hash=content_hash,
        )

    assert action == "disk_write_skipped"
    assert path.stat().st_mtime_ns == mtime_before


def test_write_disk_catalog_rewrites_when_schema_changes(tmp_path: Path) -> None:
    slug = "http___localhost_4789"
    old_tools = [{"name": "tools.demo", "description": "Demo", "input_schema": {}}]
    new_tools = [
        {
            "name": "tools.demo",
            "description": "Demo",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    ]
    old_hash = raw_catalog_content_hash(old_tools)
    cache_dir = tmp_path / "executor-catalog"
    path = cache_dir / f"{slug}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "executor_url": "http://localhost:4789",
                "catalog_content_hash": old_hash,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "tool_count": 1,
                "tools": old_tools,
            },
        ),
        encoding="utf-8",
    )
    mtime_before = path.stat().st_mtime_ns
    new_hash = raw_catalog_content_hash(new_tools)

    with patch(
        "cyt.executor.catalog_disk.executor_catalog_cache_dir",
        return_value=cache_dir,
    ):
        action = write_disk_catalog(
            slug,
            executor_url="http://localhost:4789",
            tools=new_tools,
            content_hash=new_hash,
        )

    assert action == "disk_write_updated"
    assert path.stat().st_mtime_ns != mtime_before
    with patch(
        "cyt.executor.catalog_disk.executor_catalog_cache_dir",
        return_value=cache_dir,
    ):
        stored = read_disk_catalog(slug)
    assert stored is not None
    assert stored["catalog_content_hash"] == new_hash
    assert stored["tools"] == new_tools


def test_write_disk_catalog_stores_executor_mcp_block(tmp_path: Path) -> None:
    slug = "http___localhost_4789"
    tools = [{"name": "tools.demo", "description": "Demo", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    executor = {
        "tools_list": [{"name": "execute", "description": "Run code"}],
        "execute_skill": "# execute\n\nWorkflow...",
    }
    cache_dir = tmp_path / "executor-catalog"

    with patch(
        "cyt.executor.catalog_disk.executor_catalog_cache_dir",
        return_value=cache_dir,
    ):
        action = write_disk_catalog(
            slug,
            executor_url="http://localhost:4789",
            tools=tools,
            content_hash=content_hash,
            executor=executor,
        )
        stored = read_disk_catalog(slug)

    assert action == "disk_write_created"
    assert stored is not None
    assert stored["executor"] == executor
    assert stored["executor_content_hash"] == raw_executor_mcp_content_hash(executor)


def test_write_disk_catalog_preserves_executor_when_omitted(tmp_path: Path) -> None:
    slug = "http___localhost_4789"
    tools = [{"name": "tools.demo", "description": "Demo", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    executor = {
        "tools_list": [{"name": "skills"}],
        "execute_skill": "cached skill",
    }
    cache_dir = tmp_path / "executor-catalog"
    path = cache_dir / f"{slug}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "executor_url": "http://localhost:4789",
                "catalog_content_hash": "old-hash",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "tool_count": 1,
                "tools": tools,
                "executor": executor,
                "executor_content_hash": raw_executor_mcp_content_hash(executor),
            },
        ),
        encoding="utf-8",
    )

    with patch(
        "cyt.executor.catalog_disk.executor_catalog_cache_dir",
        return_value=cache_dir,
    ):
        action = write_disk_catalog(
            slug,
            executor_url="http://localhost:4789",
            tools=tools,
            content_hash=content_hash,
        )
        stored = read_disk_catalog(slug)

    assert action == "disk_write_updated"
    assert stored is not None
    assert stored["executor"] == executor


def test_write_disk_catalog_skips_when_tools_and_executor_unchanged(tmp_path: Path) -> None:
    slug = "http___localhost_4789"
    tools = [{"name": "tools.demo", "description": "Demo", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    executor = {"tools_list": [{"name": "execute"}], "execute_skill": "guide"}
    executor_hash = raw_executor_mcp_content_hash(executor)
    cache_dir = tmp_path / "executor-catalog"
    path = cache_dir / f"{slug}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "executor_url": "http://localhost:4789",
                "catalog_content_hash": content_hash,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "tool_count": 1,
                "tools": tools,
                "executor": executor,
                "executor_content_hash": executor_hash,
            },
        ),
        encoding="utf-8",
    )
    mtime_before = path.stat().st_mtime_ns

    with patch(
        "cyt.executor.catalog_disk.executor_catalog_cache_dir",
        return_value=cache_dir,
    ):
        action = write_disk_catalog(
            slug,
            executor_url="http://localhost:4789",
            tools=tools,
            content_hash=content_hash,
            executor=executor,
        )

    assert action == "disk_write_skipped"
    assert path.stat().st_mtime_ns == mtime_before


def test_parse_jsonrpc_body_supports_sse() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n',
    )
    assert _parse_jsonrpc_body(response) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"ok": True},
    }


def test_skill_text_from_call_result() -> None:
    text = _skill_text_from_call_result(
        {
            "result": {
                "content": [{"type": "text", "text": "# execute\n\nUse tools.search()"}],
            },
        },
    )
    assert text.startswith("# execute")


def test_fetch_executor_tools_blocking_normalizes_list_and_schema() -> None:
    expected = [
        {
            "name": "tools.demo.org.default.search",
            "description": "Search files",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    ]
    mcp_cache = {
        "tools_list": [{"name": "execute"}],
        "execute_skill": "# execute",
    }

    with (
        patch(
            "cyt.executor.http.resolve_credential",
            return_value=("secret-token", "keyring"),
        ),
        patch(
            "cyt.executor.http._load_catalog_from_disk",
            return_value=False,
        ),
        patch(
            "cyt.executor.http.asyncio.run",
            side_effect=lambda coro: (
                coro.close(),
                (expected, mcp_cache),
            )[1],
        ) as run_mock,
        patch(
            "cyt.executor.http.write_disk_catalog",
        ) as write_mock,
    ):
        tools = fetch_executor_tools(_CONFIG, blocking=True)

    assert tools == expected
    assert run_mock.call_count == 2
    write_mock.assert_called_once()
    assert write_mock.call_args.kwargs["executor"] == mcp_cache


def test_fetch_executor_tools_blocking_uses_memory_cache_without_network() -> None:
    cached = [{"name": "tools.cached.tool", "description": "Cached"}]
    from cyt.executor import http as executor_http

    with patch(
        "cyt.executor.http.resolve_credential",
        return_value=("token", "keyring"),
    ):
        key = executor_http._cache_key_for_config(_CONFIG, "token")
        state = executor_http._get_state(key)
        state.tools = cached
        state.executor_mcp = {
            "tools_list": [{"name": "execute"}],
            "execute_skill": "# execute",
        }
        state.updated_at = time.monotonic()

        with patch(
            "cyt.executor.http.asyncio.run",
        ) as run_mock:
            tools = fetch_executor_tools(_CONFIG, blocking=True)

    assert tools == cached
    run_mock.assert_not_called()


def test_fetch_executor_tools_blocking_returns_stale_on_http_error() -> None:
    stale = [{"name": "tools.cached.tool", "description": "Cached"}]

    def _raise_http_error(coro: Coroutine[Any, Any, Any]) -> list[dict[str, object]]:
        coro.close()
        raise httpx.HTTPError("boom")

    with (
        patch(
            "cyt.executor.http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.executor.http._load_catalog_from_disk",
            return_value=False,
        ),
        patch(
            "cyt.executor.http.asyncio.run",
            side_effect=_raise_http_error,
        ),
    ):
        assert fetch_executor_tools(_CONFIG, blocking=True) == []

    with (
        patch(
            "cyt.executor.http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.executor.http._load_catalog_from_disk",
            return_value=False,
        ),
        patch(
            "cyt.executor.http.asyncio.run",
            side_effect=_raise_http_error,
        ),
        patch(
            "cyt.executor.http._snapshot_tools",
            return_value=stale,
        ),
    ):
        assert fetch_executor_tools(_CONFIG, blocking=True) == stale


def test_load_executor_tools_never_blocks_on_background_refresh() -> None:
    stale = [{"name": "tools.stale.tool", "description": "Old catalog"}]
    from cyt.executor import http as executor_http

    with (
        patch(
            "cyt.executor.http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.executor.http._load_catalog_from_disk",
            return_value=False,
        ),
        patch(
            "cyt.executor.http._ensure_scheduler_started",
        ) as scheduler_mock,
    ):
        key = executor_http._cache_key_for_config(_CONFIG, "token")
        state = executor_http._get_state(key)
        state.tools = stale
        state.executor_mcp = {
            "tools_list": [{"name": "execute"}],
            "execute_skill": "# execute",
        }
        state.updated_at = time.monotonic()

        tools = load_executor_tools(_CONFIG, allow_prompt=False, blocking=False)

    assert tools == stale
    scheduler_mock.assert_called_once()


def test_get_executor_catalog_loads_disk_on_cold_start(tmp_path: Path) -> None:
    slug = "http___localhost_4789__EXECUTOR_TOKEN"
    tools = [{"name": "tools.disk", "description": "From disk", "input_schema": {}}]
    executor = {
        "tools_list": [{"name": "execute"}],
        "execute_skill": "# execute",
    }
    content_hash = raw_catalog_content_hash(tools)
    cache_dir = tmp_path / "executor-catalog"
    path = cache_dir / f"{slug}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "executor_url": "http://localhost:4789",
                "catalog_content_hash": content_hash,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "tool_count": 1,
                "tools": tools,
                "executor": executor,
                "executor_content_hash": raw_executor_mcp_content_hash(executor),
            },
        ),
        encoding="utf-8",
    )

    with (
        patch(
            "cyt.executor.http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.executor.catalog_disk.executor_catalog_cache_dir",
            return_value=cache_dir,
        ),
        patch(
            "cyt.executor.http._ensure_scheduler_started",
        ),
        patch(
            "cyt.executor.http.asyncio.run",
        ) as run_mock,
    ):
        loaded = get_executor_catalog(_CONFIG, allow_prompt=False, blocking=False)

    assert loaded == tools
    run_mock.assert_not_called()


def test_executor_catalog_slug_is_pipeline_agnostic() -> None:
    """bm25/llm/rerank configs share the same raw executor disk slug."""
    base = {
        "pruning": {
            "tools": {
                "hook": {
                    "executor_url": "http://localhost:4789",
                    "executor_token_var": "EXECUTOR_TOKEN",
                },
                "sequence": ["bm25"],
            },
        },
    }
    llm_config = {
        **base,
        "pruning": {
            **base["pruning"],
            "tools": {**base["pruning"]["tools"], "sequence": ["llm"]},
        },
    }
    rerank_config = {
        **base,
        "pruning": {
            **base["pruning"],
            "tools": {**base["pruning"]["tools"], "sequence": ["rerank"]},
        },
    }
    slug = normalize_executor_url_slug(
        "http://localhost:4789",
        token_var="EXECUTOR_TOKEN",
    )
    for cfg in (base, llm_config, rerank_config):
        with patch(
            "cyt.executor.http.resolve_credential",
            return_value=("token", "keyring"),
        ):
            key = _cache_key_for_config(cfg, "token")
        assert key.slug == slug


def test_schedule_executor_catalog_refresh_starts_scheduler() -> None:
    with (
        patch(
            "cyt.executor.cache_scheduler._executor_runtime_active",
            return_value=True,
        ),
        patch(
            "cyt.executor.cache_scheduler.tools_hook_executor_url",
            return_value="http://localhost:4789",
        ),
        patch(
            "cyt.executor.cache_scheduler._resolve_executor_token",
            return_value="token",
        ),
        patch(
            "cyt.executor.cache_scheduler.start_executor_cache_scheduler",
        ) as start_mock,
        patch(
            "cyt.executor.cache_scheduler._get_scheduler_state",
        ) as state_mock,
    ):
        state = state_mock.return_value
        state.thread = None
        schedule_executor_catalog_refresh(_CONFIG, force=False)

    start_mock.assert_called_once()


def test_get_executor_mcp_cache_loads_from_disk_without_network(tmp_path: Path) -> None:
    slug = "http___localhost_4789__EXECUTOR_TOKEN"
    tools = [{"name": "tools.disk", "description": "From disk", "input_schema": {}}]
    executor = {
        "tools_list": [
            {
                "name": "execute",
                "description": "Run code",
                "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}}},
            },
        ],
        "execute_skill": "# execute\n\nUse tools.search()",
    }
    content_hash = raw_catalog_content_hash(tools)
    cache_dir = tmp_path / "executor-catalog"
    path = cache_dir / f"{slug}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "executor_url": "http://localhost:4789",
                "catalog_content_hash": content_hash,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "tool_count": 1,
                "tools": tools,
                "executor": executor,
                "executor_content_hash": raw_executor_mcp_content_hash(executor),
            },
        ),
        encoding="utf-8",
    )

    with (
        patch(
            "cyt.executor.http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.executor.catalog_disk.executor_catalog_cache_dir",
            return_value=cache_dir,
        ),
        patch(
            "cyt.executor.http._ensure_scheduler_started",
        ) as schedule_mock,
        patch(
            "cyt.executor.http.asyncio.run",
        ) as run_mock,
    ):
        from cyt.executor.http import get_executor_mcp_cache

        loaded = get_executor_mcp_cache(_CONFIG, allow_prompt=False)

    assert loaded == executor
    run_mock.assert_not_called()
    schedule_mock.assert_called_once()

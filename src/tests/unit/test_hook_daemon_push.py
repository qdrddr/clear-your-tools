"""Tests for cyt-mcp hook daemon catalog push client."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cyt.hook.catalog_registry import catalog_for_hook, clear_catalog_registry
from cyt_mcp.config import (
    AggregatorConfig,
    load_aggregator_config,
    sample_aggregator_config,
)
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.hook_daemon_push import (
    _RETRY_DELAYS_SECONDS,
    PushContext,
    _build_register_payload,
    _can_push_to_registry,
    _instance_key,
    _last_permissions_revision,
    _maybe_reload_permissions,
    _push_once,
    schedule_catalog_push,
)
from cyt_mcp.runtime_cache import RuntimeToolCache
from tests.support.tier_capture_fixtures import load_meta_tools_not_reported


class _TrackingConfigHolder(ConfigHolder):
    def __init__(self, agg_config: AggregatorConfig) -> None:
        super().__init__(agg_config)
        self.reload_calls = 0

    def reload_mcp_deny(self) -> tuple[str, ...]:
        self.reload_calls += 1
        self.config = replace(self.config, mcp_deny=("hedl/hedl_batch",))
        return self.config.mcp_deny


def _config(
    *,
    workspace_root: Path | None = None,
    aggregator_path: Path | None = None,
) -> AggregatorConfig:
    return sample_aggregator_config(
        catalog_scope="workspace" if workspace_root is not None else "user",
        workspace_root=workspace_root,
        aggregator_path=aggregator_path,
    )


def test_instance_key_requires_workspace_path(tmp_path: Path) -> None:
    user_config = _config()
    ws_config = _config(workspace_root=tmp_path)
    assert _instance_key(user_config) == "cursor:workspace::usr"
    assert _instance_key(ws_config) == f"cursor:workspace:{tmp_path}:ws"


def test_can_push_to_registry_requires_workspace_root(tmp_path: Path) -> None:
    assert _can_push_to_registry(_config()) is False
    assert _can_push_to_registry(_config(workspace_root=tmp_path)) is True


def test_push_once_sends_full_then_hash_only(tmp_path: Path) -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_a", "inputSchema": {"type": "object"}}])
    config = _config(workspace_root=tmp_path)
    calls: list[dict[str, object]] = []

    def fake_post(_url: str, payload: dict[str, object]) -> tuple[int, dict[str, object] | None]:
        calls.append(payload)
        if "tools" in payload:
            return 200, {"status": "stored", "permissions_revision": 0}
        return 204, {"status": "unchanged", "permissions_revision": 0}

    with (
        patch(
            "cyt_mcp.hook_daemon_push.resolve_hook_register_url",
            return_value="http://127.0.0.1:8834/hook/catalog/register",
        ),
        patch("cyt_mcp.hook_daemon_push._post_json", side_effect=fake_post),
    ):
        ok1, _rev1 = _push_once(config, cache)
        ok2, _rev2 = _push_once(config, cache)
        assert ok1 is True
        assert ok2 is True

    assert calls[0]["scope"] == "workspace"
    assert calls[0]["workspace_root"] == str(tmp_path)
    assert "tools" in calls[0]
    assert "tools" not in calls[1]


@pytest.mark.parametrize("meta_tool_name", load_meta_tools_not_reported())
def test_build_register_payload_excludes_meta_tool_names(
    tmp_path: Path,
    meta_tool_name: str,
) -> None:
    cache = RuntimeToolCache()
    cache.replace(
        [
            {"name": meta_tool_name, "inputSchema": {"type": "object"}},
            {"name": "semble_search", "inputSchema": {"type": "object"}},
        ],
    )
    body = _build_register_payload(_config(workspace_root=tmp_path), cache, include_tools=True)
    names = [str(tool.get("name") or "") for tool in body["tools"] if isinstance(tool, dict)]
    assert meta_tool_name not in names
    assert names == ["semble_search"]


def test_register_catalog_from_push_payload_excludes_meta_tools(tmp_path: Path) -> None:
    from cyt.hook.catalog_registry import register_catalog

    cache = RuntimeToolCache()
    cache.replace(
        [
            {"name": name, "inputSchema": {"type": "object"}}
            for name in load_meta_tools_not_reported()
        ]
        + [{"name": "semble_search", "inputSchema": {"type": "object"}}],
    )
    clear_catalog_registry()
    body = _build_register_payload(_config(workspace_root=tmp_path), cache, include_tools=True)
    register_catalog(body)

    merged = catalog_for_hook("cursor", tmp_path)
    names = {str(tool.get("name") or "") for tool in merged}
    assert names == {"semble_search"}
    for meta_name in load_meta_tools_not_reported():
        assert meta_name not in names


def test_push_once_excludes_get_tool_definitions_from_daemon_payload(tmp_path: Path) -> None:
    from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME

    cache = RuntimeToolCache()
    cache.replace(
        [
            {"name": MCP_WIRE_SEARCH_TOOL_NAME, "inputSchema": {"type": "object"}},
            {"name": "semble_search", "inputSchema": {"type": "object"}},
        ],
    )
    config = _config(workspace_root=tmp_path)
    calls: list[dict[str, object]] = []

    def fake_post(_url: str, payload: dict[str, object]) -> tuple[int, dict[str, object] | None]:
        calls.append(payload)
        return 200, {"status": "stored", "permissions_revision": 0}

    with (
        patch(
            "cyt_mcp.hook_daemon_push.resolve_hook_register_url",
            return_value="http://127.0.0.1:8834/hook/catalog/register",
        ),
        patch("cyt_mcp.hook_daemon_push._post_json", side_effect=fake_post),
    ):
        ok, _rev = _push_once(config, cache)
        assert ok is True

    tools = calls[0]["tools"]
    assert isinstance(tools, list)
    names = [str(tool.get("name") or "") for tool in tools if isinstance(tool, dict)]
    assert names == ["semble_search"]


def test_push_once_skipped_without_workspace_root() -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_a", "inputSchema": {"type": "object"}}])
    config = _config()

    with patch("cyt_mcp.hook_daemon_push._post_json") as fake_post:
        ok, _rev = _push_once(config, cache)
        assert ok is False
        fake_post.assert_not_called()


def test_push_once_deferred_when_catalog_empty(tmp_path: Path) -> None:
    cache = RuntimeToolCache()
    config = _config(workspace_root=tmp_path)

    with (
        patch(
            "cyt_mcp.hook_daemon_push.resolve_hook_register_url",
            return_value="http://127.0.0.1:8834/hook/catalog/register",
        ),
        patch("cyt_mcp.hook_daemon_push._post_json") as fake_post,
    ):
        ok, _rev = _push_once(config, cache)
        assert ok is False
        fake_post.assert_not_called()


@pytest.mark.asyncio
async def test_retry_push_loop_uses_updated_push_context(tmp_path: Path) -> None:
    bootstrap_cache = RuntimeToolCache()
    workspace_cache = RuntimeToolCache()
    workspace_cache.replace([{"name": "tool_ws", "inputSchema": {"type": "object"}}])
    config = _config(workspace_root=tmp_path)
    key = _instance_key(config)

    bootstrap_context = PushContext(
        config_holder=ConfigHolder(config),
        cache=bootstrap_cache,
        server=MagicMock(),
    )
    workspace_context = PushContext(
        config_holder=ConfigHolder(config),
        cache=workspace_cache,
        server=MagicMock(),
    )

    from cyt_mcp import hook_daemon_push as push_mod

    push_mod._push_contexts[key] = bootstrap_context
    attempts: list[int] = []

    def fake_push_once(_config: AggregatorConfig, cache: RuntimeToolCache) -> tuple[bool, int]:
        attempts.append(len(cache.snapshot()))
        if len(attempts) == 1:
            push_mod._push_contexts[key] = workspace_context
            return False, 0
        return len(cache.snapshot()) > 0, 0

    async def stop_after_success_sleep(_delay: float) -> None:
        if _delay >= 10.0:
            raise asyncio.CancelledError()

    with (
        patch.object(push_mod, "_push_once", side_effect=fake_push_once),
        patch.object(push_mod, "_maybe_reload_permissions", new=AsyncMock()),
        patch.object(asyncio, "sleep", side_effect=stop_after_success_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await push_mod._retry_push_loop(bootstrap_context)

    assert attempts == [0, 1]


def test_push_once_hash_only_404_triggers_full_resend(tmp_path: Path) -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_b", "inputSchema": {"type": "object"}}])
    config = _config(workspace_root=tmp_path)
    calls: list[dict[str, object]] = []

    from cyt_mcp.catalog import catalog_tools_content_hash

    content_hash = catalog_tools_content_hash(cache.snapshot())
    instance_key = _instance_key(config)

    def fake_post(_url: str, payload: dict[str, object]) -> tuple[int, dict[str, object] | None]:
        calls.append(payload)
        if "tools" in payload:
            return 200, {"status": "stored", "permissions_revision": 0}
        return 404, {"error": "unknown hash"}

    with (
        patch(
            "cyt_mcp.hook_daemon_push.resolve_hook_register_url",
            return_value="http://127.0.0.1:8834/hook/catalog/register",
        ),
        patch("cyt_mcp.hook_daemon_push._post_json", side_effect=fake_post),
        patch.dict(
            "cyt_mcp.hook_daemon_push._last_success_hash",
            {instance_key: content_hash},
            clear=False,
        ),
    ):
        ok, _rev = _push_once(config, cache)
        assert ok is True

    assert len(calls) == 2
    assert "tools" not in calls[0]
    assert "tools" in calls[1]


def test_push_sync_with_retry_uses_backoff_delays(tmp_path: Path) -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_c", "inputSchema": {"type": "object"}}])
    config = _config(workspace_root=tmp_path)
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_push_once(_config: AggregatorConfig, _cache: RuntimeToolCache) -> tuple[bool, int]:
        attempts["count"] += 1
        return (attempts["count"] >= 3, 0)

    with (
        patch("cyt_mcp.hook_daemon_push._push_once", side_effect=fake_push_once),
        patch("time.sleep", side_effect=lambda delay: sleeps.append(delay)),
    ):
        from cyt_mcp.hook_daemon_push import _push_sync_with_retry

        _push_sync_with_retry(config, cache)

    assert attempts["count"] == 3
    assert sleeps == [_RETRY_DELAYS_SECONDS[0], _RETRY_DELAYS_SECONDS[1]]


@pytest.mark.asyncio
async def test_schedule_catalog_push_is_non_blocking(tmp_path: Path) -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_d", "inputSchema": {"type": "object"}}])
    config = _config(workspace_root=tmp_path)
    started = asyncio.Event()

    async def fake_retry_loop(_config: AggregatorConfig, _cache: RuntimeToolCache) -> None:
        started.set()

    with patch("cyt_mcp.hook_daemon_push._retry_push_loop_legacy", side_effect=fake_retry_loop):
        schedule_catalog_push(cache, config)
        await asyncio.wait_for(started.wait(), timeout=1.0)


def test_load_aggregator_config_infers_workspace_scope(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    agg_dir = repo / ".cursor" / "cyt" / "config"
    agg_dir.mkdir(parents=True)
    agg_path = agg_dir / "mcp-aggregator.yaml"
    agg_path.write_text(
        "\n".join(
            [
                "agent: cursor",
                "catalog_scope: workspace",
                "transport: stdio",
            ],
        ),
        encoding="utf-8",
    )
    mcp_dir = repo / ".cursor" / "cyt" / "mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "cursor.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    config = load_aggregator_config(
        agent="cursor",
        aggregator_path=agg_path,
        workspace_folder=repo,
    )
    assert config.catalog_scope == "workspace"
    assert config.workspace_root == repo.resolve()


@pytest.mark.asyncio
async def test_maybe_reload_permissions_on_revision_bump(tmp_path: Path) -> None:
    config = _config(workspace_root=tmp_path)
    key = _instance_key(config)
    _last_permissions_revision.clear()

    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_a", "inputSchema": {"type": "object"}}])

    config_holder = MagicMock(spec=ConfigHolder)
    config_holder.config = config
    config_holder.reload_mcp_deny = MagicMock()

    middleware = MagicMock()
    middleware.notify_all_sessions = AsyncMock()

    context = PushContext(
        config_holder=config_holder,
        cache=cache,
        server=MagicMock(),
        list_changed_middleware=middleware,
    )

    with patch("cyt_mcp.catalog_build.refresh_catalog_cache", new_callable=AsyncMock) as refresh:
        await _maybe_reload_permissions(key=key, revision=0, context=context)
        config_holder.reload_mcp_deny.assert_not_called()
        middleware.notify_all_sessions.assert_not_awaited()
        refresh.assert_not_awaited()

        await _maybe_reload_permissions(key=key, revision=1, context=context)
        config_holder.reload_mcp_deny.assert_called_once()
        middleware.notify_all_sessions.assert_not_awaited()
        refresh.assert_not_awaited()

        await _maybe_reload_permissions(key=key, revision=1, context=context)
        assert config_holder.reload_mcp_deny.call_count == 1


@pytest.mark.asyncio
async def test_maybe_reload_permissions_notifies_when_deny_changes_without_hash_change(
    tmp_path: Path,
) -> None:
    config = _config(workspace_root=tmp_path)
    key = _instance_key(config)
    _last_permissions_revision.clear()

    cache = RuntimeToolCache()
    cache.replace([{"name": "hedl_batch", "inputSchema": {"type": "object"}}])

    config_holder = _TrackingConfigHolder(config)

    middleware = MagicMock()
    middleware.notify_all_sessions = AsyncMock()

    context = PushContext(
        config_holder=config_holder,
        cache=cache,
        server=MagicMock(),
        list_changed_middleware=middleware,
    )

    async def noop_refresh(
        _server: object,
        runtime_cache: RuntimeToolCache,
        _config: object,
        *,
        skip_push: bool = False,
    ) -> None:
        del _server, runtime_cache, _config, skip_push

    with patch("cyt_mcp.catalog_build.refresh_catalog_cache", side_effect=noop_refresh):
        await _maybe_reload_permissions(key=key, revision=1, context=context)

    assert config_holder.reload_calls == 1
    middleware.notify_all_sessions.assert_awaited_once()


def test_sync_push_notifies_on_deny_only_change(tmp_path: Path) -> None:
    config = _config(workspace_root=tmp_path)
    key = _instance_key(config)
    _last_permissions_revision.clear()

    cache = RuntimeToolCache()
    cache.replace([{"name": "hedl_batch", "inputSchema": {"type": "object"}}])

    holder = _TrackingConfigHolder(config)
    middleware = MagicMock()
    middleware.notify_all_sessions = AsyncMock()

    context = PushContext(
        config_holder=holder,
        cache=cache,
        server=MagicMock(),
        list_changed_middleware=middleware,
    )
    from cyt_mcp import hook_daemon_push as push_mod

    push_mod._push_contexts[key] = context
    push_mod._last_permissions_revision[key] = 0

    async def noop_refresh(
        _server: object,
        runtime_cache: RuntimeToolCache,
        _config: object,
        *,
        skip_push: bool = False,
    ) -> None:
        del _server, runtime_cache, _config, skip_push

    def fake_push_once(_config: AggregatorConfig, _cache: RuntimeToolCache) -> tuple[bool, int]:
        return True, 1

    with (
        patch.object(push_mod, "_push_once", side_effect=fake_push_once),
        patch("cyt_mcp.catalog_build.refresh_catalog_cache", side_effect=noop_refresh),
        patch.object(
            push_mod,
            "notify_all_sessions_list_changed",
            new=AsyncMock(),
        ) as notify,
    ):
        push_mod._push_sync_with_retry(config, cache)

    assert holder.reload_calls == 1
    notify.assert_awaited_once_with(middleware)

"""Tests for cyt-mcp hook daemon catalog push client."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cyt_mcp.config import (
    AggregatorConfig,
    load_aggregator_config,
    sample_aggregator_config,
)
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.hook_daemon_push import (
    _RETRY_DELAYS_SECONDS,
    PushContext,
    _can_push_to_registry,
    _instance_key,
    _last_permissions_revision,
    _maybe_reload_permissions,
    _push_once,
    schedule_catalog_push,
)
from cyt_mcp.runtime_cache import RuntimeToolCache


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
    assert _instance_key(user_config) == "cursor:workspace:"
    assert _instance_key(ws_config) == f"cursor:workspace:{tmp_path}"


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


def test_push_once_skipped_without_workspace_root() -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_a", "inputSchema": {"type": "object"}}])
    config = _config()

    with patch("cyt_mcp.hook_daemon_push._post_json") as fake_post:
        ok, _rev = _push_once(config, cache)
        assert ok is False
        fake_post.assert_not_called()


def test_push_once_hash_only_404_triggers_full_resend(tmp_path: Path) -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_b", "inputSchema": {"type": "object"}}])
    config = _config(workspace_root=tmp_path)
    calls: list[dict[str, object]] = []

    from cyt_mcp.catalog import catalog_tools_content_hash

    content_hash = catalog_tools_content_hash(cache.snapshot())
    instance_key = f"cursor:workspace:{tmp_path}"

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

    async def fake_refresh(
        _server: object,
        runtime_cache: RuntimeToolCache,
        _config: object,
        *,
        skip_push: bool = False,
    ) -> None:
        del skip_push
        runtime_cache.replace([{"name": "tool_b", "inputSchema": {"type": "object"}}])

    with patch("cyt_mcp.catalog_build.refresh_catalog_cache", side_effect=fake_refresh):
        await _maybe_reload_permissions(key=key, revision=0, context=context)
        config_holder.reload_mcp_deny.assert_not_called()
        middleware.notify_all_sessions.assert_not_awaited()

        await _maybe_reload_permissions(key=key, revision=1, context=context)
        config_holder.reload_mcp_deny.assert_called_once()
        middleware.notify_all_sessions.assert_awaited_once()

        await _maybe_reload_permissions(key=key, revision=1, context=context)
        assert config_holder.reload_mcp_deny.call_count == 1

"""Tests for cyt-mcp hook daemon catalog push client."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt_mcp.config import AggregatorConfig, CatalogScope, HttpSettings, load_aggregator_config
from cyt_mcp.hook_daemon_push import (
    _RETRY_DELAYS_SECONDS,
    _instance_key,
    _push_once,
    schedule_catalog_push,
)
from cyt_mcp.runtime_cache import RuntimeToolCache


def _config(
    *,
    catalog_scope: CatalogScope = "global",
    workspace_root: Path | None = None,
    aggregator_path: Path | None = None,
) -> AggregatorConfig:
    return AggregatorConfig(
        agent="cursor",
        mcp_servers={},
        transport="stdio",
        http=HttpSettings(
            host="127.0.0.1",
            port=8765,
            mcp_path="/mcp",
            catalog_path="/catalog",
        ),
        codex_stubs_include_description=False,
        verify_only=False,
        aggregator_path=aggregator_path or Path("~/.config/cyt/mcp-aggregator.yaml"),
        agent_mcp_path=Path("~/.config/cyt/mcp/cursor.json"),
        catalog_scope=catalog_scope,
        workspace_root=workspace_root,
    )


def test_instance_key_includes_scope_and_workspace(tmp_path: Path) -> None:
    global_config = _config()
    ws_config = _config(catalog_scope="workspace", workspace_root=tmp_path)
    assert _instance_key(global_config) == "cursor:global:"
    assert _instance_key(ws_config) == f"cursor:workspace:{tmp_path}"


def test_push_once_sends_full_then_hash_only() -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_a", "inputSchema": {"type": "object"}}])
    config = _config()
    calls: list[dict[str, object]] = []

    def fake_post(_url: str, payload: dict[str, object]) -> int:
        calls.append(payload)
        if "tools" in payload:
            return 200
        return 204

    with (
        patch(
            "cyt_mcp.hook_daemon_push.resolve_hook_register_url",
            return_value="http://127.0.0.1:8834/hook/catalog/register",
        ),
        patch("cyt_mcp.hook_daemon_push._post_json", side_effect=fake_post),
    ):
        assert _push_once(config, cache) is True
        assert _push_once(config, cache) is True

    assert "tools" in calls[0]
    assert "tools" not in calls[1]


def test_push_once_hash_only_404_triggers_full_resend() -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_b", "inputSchema": {"type": "object"}}])
    config = _config()
    calls: list[dict[str, object]] = []

    from cyt_mcp.catalog import catalog_tools_content_hash

    content_hash = catalog_tools_content_hash(cache.snapshot())

    def fake_post(_url: str, payload: dict[str, object]) -> int:
        calls.append(payload)
        if "tools" in payload:
            return 200
        return 404

    with (
        patch(
            "cyt_mcp.hook_daemon_push.resolve_hook_register_url",
            return_value="http://127.0.0.1:8834/hook/catalog/register",
        ),
        patch("cyt_mcp.hook_daemon_push._post_json", side_effect=fake_post),
        patch.dict(
            "cyt_mcp.hook_daemon_push._last_success_hash",
            {"cursor:global:": content_hash},
            clear=False,
        ),
    ):
        assert _push_once(config, cache) is True

    assert len(calls) == 2
    assert "tools" not in calls[0]
    assert "tools" in calls[1]


def test_push_sync_with_retry_uses_backoff_delays() -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_c", "inputSchema": {"type": "object"}}])
    config = _config()
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_push_once(_config: AggregatorConfig, _cache: RuntimeToolCache) -> bool:
        attempts["count"] += 1
        return attempts["count"] >= 3

    with (
        patch("cyt_mcp.hook_daemon_push._push_once", side_effect=fake_push_once),
        patch("time.sleep", side_effect=lambda delay: sleeps.append(delay)),
    ):
        from cyt_mcp.hook_daemon_push import _push_sync_with_retry

        _push_sync_with_retry(config, cache)

    assert attempts["count"] == 3
    assert sleeps == [_RETRY_DELAYS_SECONDS[0], _RETRY_DELAYS_SECONDS[1]]


@pytest.mark.asyncio
async def test_schedule_catalog_push_is_non_blocking() -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "tool_d", "inputSchema": {"type": "object"}}])
    config = _config()
    started = asyncio.Event()

    async def fake_retry_loop(_config: AggregatorConfig, _cache: RuntimeToolCache) -> None:
        started.set()

    with patch("cyt_mcp.hook_daemon_push._retry_push_loop", side_effect=fake_retry_loop):
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

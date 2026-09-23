"""Tests for cyt-mcp catalog hydrate/refresh behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cyt_mcp.catalog_build import (
    hydrate_offerings_cache,
    hydrate_runtime_cache,
    offerings_runtime_key,
    refresh_catalog_cache,
)
from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.offerings_cache import OfferingsCache, OfferingsSnapshot
from cyt_mcp.runtime_cache import RuntimeToolCache


@pytest.mark.asyncio
async def test_refresh_catalog_cache_skips_backend_list_when_cache_warm() -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "backend-a_tool", "inputSchema": {"type": "object"}}])
    server = MagicMock()
    server._list_tools = AsyncMock(return_value=[])

    await refresh_catalog_cache(server, cache, sample_aggregator_config())

    server._list_tools.assert_not_called()
    assert len(cache.snapshot()) == 1


@pytest.mark.asyncio
async def test_refresh_catalog_cache_force_still_lists_backends() -> None:
    cache = RuntimeToolCache()
    cache.replace([{"name": "backend-a_tool", "inputSchema": {"type": "object"}}])
    tool = MagicMock()
    tool.to_mcp_tool.return_value = MagicMock(
        name="backend-b_tool",
        inputSchema={"type": "object"},
        description="",
        title=None,
        annotations=None,
        execution=None,
        meta=None,
    )
    server = MagicMock()
    server._list_tools = AsyncMock(return_value=[tool])

    config = sample_aggregator_config(
        mcp_servers={"backend-b": {"command": "echo"}},
    )

    await refresh_catalog_cache(server, cache, config, force=True)

    server._list_tools.assert_awaited_once()
    assert len(cache.snapshot()) == 1


def test_workspace_disk_slug_includes_workspace_path(
    tmp_path: Path,
) -> None:
    from cyt_mcp import catalog_build as catalog_build_mod

    ws_a = tmp_path / "repo-a"
    ws_b = tmp_path / "repo-b"
    for ws in (ws_a, ws_b):
        (ws / ".agents" / "cyt" / "config" / "mcp").mkdir(parents=True)
        (ws / ".agents" / "cyt" / "config" / "mcp-config.yaml").write_text(
            "catalog_scope: workspace\n",
            encoding="utf-8",
        )
        (ws / ".agents" / "cyt" / "config" / "mcp" / "cursor.json").write_text(
            '{"mcpServers": {}}',
            encoding="utf-8",
        )
    cfg_a = sample_aggregator_config(
        workspace_root=ws_a,
        catalog_scope="workspace",
        aggregator_path=ws_a / ".agents" / "cyt" / "config" / "mcp-config.yaml",
    )
    cfg_b = sample_aggregator_config(
        workspace_root=ws_b,
        catalog_scope="workspace",
        aggregator_path=ws_b / ".agents" / "cyt" / "config" / "mcp-config.yaml",
    )
    slug_a = catalog_build_mod.disk_catalog_slug_for_config(cfg_a)
    slug_b = catalog_build_mod.disk_catalog_slug_for_config(cfg_b)
    assert slug_a is not None and slug_b is not None
    assert slug_a != slug_b


def test_offerings_runtime_key_differs_for_user_and_workspace(
    tmp_path: Path,
) -> None:
    from cyt_mcp import catalog_build as catalog_build_mod

    ws = tmp_path / "repo"
    (ws / ".agents" / "cyt" / "config" / "mcp").mkdir(parents=True)
    (ws / ".agents" / "cyt" / "config" / "mcp-config.yaml").write_text(
        "catalog_scope: workspace\n",
        encoding="utf-8",
    )
    (ws / ".agents" / "cyt" / "config" / "mcp" / "cursor.json").write_text(
        '{"mcpServers": {}}',
        encoding="utf-8",
    )
    user_cfg = sample_aggregator_config(catalog_scope="user", workspace_root=None)
    ws_cfg = sample_aggregator_config(
        workspace_root=ws,
        catalog_scope="workspace",
        aggregator_path=ws / ".agents" / "cyt" / "config" / "mcp-config.yaml",
    )
    user_key = offerings_runtime_key(user_cfg)
    ws_key = offerings_runtime_key(ws_cfg)
    assert user_key is not None and ws_key is not None
    assert user_key != ws_key
    assert user_key == catalog_build_mod.disk_catalog_slug_for_config(user_cfg)
    assert ws_key == catalog_build_mod.disk_catalog_slug_for_config(ws_cfg)


def test_hydrate_offerings_cache_works_without_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.cyt_mcp import catalog_disk

    monkeypatch.setattr(
        catalog_disk,
        "cyt_mcp_catalog_cache_dir",
        lambda: tmp_path,
    )
    config = sample_aggregator_config(catalog_scope="user", workspace_root=None)
    key = offerings_runtime_key(config)
    assert key is not None
    from cyt_mcp.offerings_cache import persist_offerings_snapshot

    persist_offerings_snapshot(
        key,
        OfferingsSnapshot(resources=[{"uri": "gitnexus://demo", "name": "demo"}]),
    )
    cache = OfferingsCache()
    assert hydrate_offerings_cache(cache, config) is True
    assert len(cache.snapshot_or_empty(key).resources) == 1


def test_hydrate_prefers_disk_over_smaller_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.cyt_mcp.catalog_disk import write_disk_catalog
    from cyt_mcp import catalog_build as catalog_build_mod

    workspace = tmp_path / "consumer"
    workspace.mkdir()
    config = sample_aggregator_config(
        workspace_root=workspace,
        catalog_scope="workspace",
    )
    slug = catalog_build_mod.disk_catalog_slug_for_config(config)
    assert slug is not None

    write_disk_catalog(
        slug,
        agent="cursor",
        tools=[{"name": f"tool-{index}", "inputSchema": {"type": "object"}} for index in range(10)],
        content_hash="abc123",
    )
    monkeypatch.setattr(
        catalog_build_mod,
        "_catalog_tools_from_registry",
        lambda _config: [{"name": "registry-only", "inputSchema": {"type": "object"}}],
    )

    cache = RuntimeToolCache()
    assert hydrate_runtime_cache(cache, config) is True
    assert len(cache.snapshot()) == 10

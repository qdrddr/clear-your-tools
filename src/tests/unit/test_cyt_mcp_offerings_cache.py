"""Tests for non-blocking MCP offerings cache."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cyt_mcp.offerings_cache import OfferingsCache, OfferingsSnapshot


@pytest.mark.asyncio
async def test_refresh_from_server_stores_snapshot_on_main_loop() -> None:
    server = MagicMock()
    server._list_resources = AsyncMock(return_value=[{"uri": "gitnexus://x"}])
    server._list_prompts = AsyncMock(return_value=[{"name": "p1"}])
    server._list_resource_templates = AsyncMock(return_value=[])

    cache = OfferingsCache()
    mounted: list[str] = []

    def _ensure_mounted(_servers: dict[str, object]) -> None:
        mounted.append("ok")

    snapshot = await cache.refresh_from_server(
        server,
        runtime_key="ws-a",
        mcp_servers={"gitnexus": {}},
        ensure_mounted=_ensure_mounted,
    )
    assert mounted == ["ok"]
    assert len(snapshot.resources) == 1
    assert len(snapshot.prompts) == 1
    assert cache.get("ws-a") is snapshot


def test_replace_preserves_nonempty_snapshot_on_empty_regression() -> None:
    cache = OfferingsCache()
    good = OfferingsSnapshot(resources=[{"uri": "a"}])
    cache.replace("ws-a", good)
    kept = cache.replace("ws-a", OfferingsSnapshot())
    assert kept is good
    assert len(cache.snapshot_or_empty("ws-a").resources) == 1


def test_snapshot_or_empty_returns_empty_when_missing() -> None:
    cache = OfferingsCache()
    empty = cache.snapshot_or_empty("missing")
    assert empty == OfferingsSnapshot()
    cache.replace("ws-a", OfferingsSnapshot(resources=[{"uri": "a"}]))
    assert len(cache.snapshot_or_empty("ws-a").resources) == 1

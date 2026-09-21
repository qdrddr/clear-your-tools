"""Tests for non-blocking MCP offerings cache."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import Resource

from cyt_mcp.offerings_cache import (
    OfferingsCache,
    OfferingsSnapshot,
    offerings_snapshot_from_server,
    offerings_to_wire,
    persist_offerings_snapshot,
)


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


def test_offerings_snapshot_serializes_mcp_resource_for_disk(tmp_path, monkeypatch) -> None:
    from cyt.cyt_mcp import catalog_disk

    monkeypatch.setattr(
        catalog_disk,
        "cyt_mcp_catalog_cache_dir",
        lambda: tmp_path,
    )
    resource = Resource(uri="gitnexus://repo/demo/process/main", name="main")
    snapshot = offerings_snapshot_from_server(
        resources=[resource],
        prompts=[],
        resource_templates=[],
    )
    assert persist_offerings_snapshot("slug-a", snapshot) is True
    payload = json.loads((tmp_path / "offerings" / "slug-a.json").read_text(encoding="utf-8"))
    assert payload["resources"][0]["uri"] == "gitnexus://repo/demo/process/main"


def test_offerings_to_wire_restores_resource_objects() -> None:
    wired = offerings_to_wire(
        [{"uri": "gitnexus://repo/demo/process/main", "name": "main"}],
        Resource,
    )
    assert len(wired) == 1
    assert isinstance(wired[0], Resource)
    assert str(wired[0].uri) == "gitnexus://repo/demo/process/main"

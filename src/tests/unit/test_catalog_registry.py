"""Tests for cyt-mcp push catalog registry."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cyt.cyt_mcp.catalog_disk import raw_catalog_content_hash
from cyt.hook.catalog_registry import (
    RegisterStatus,
    catalog_for_hook,
    clear_catalog_registry,
    load_catalog_registry_from_disk,
    merge_catalog_for_hook,
    prune_expired_registrations,
    register_catalog,
    touch_heartbeat,
)


@pytest.fixture(autouse=True)
def _clear_registry() -> Iterator[None]:
    clear_catalog_registry()
    yield
    clear_catalog_registry()


def _register_workspace(
    ws_root: Path,
    tools: list[dict[str, Any]],
    *,
    instance_id: str = "pid:1",
) -> None:
    content_hash = raw_catalog_content_hash(tools)
    result = register_catalog(
        {
            "agent": "cursor",
            "scope": "workspace",
            "workspace_root": str(ws_root),
            "instance_id": instance_id,
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    assert result.status == RegisterStatus.STORED


def test_register_rejects_legacy_global_scope() -> None:
    tools = [{"name": "global_tool", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    result = register_catalog(
        {
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    assert result.status == RegisterStatus.INVALID
    assert result.http_status == 400


def test_workspace_lookup_only(tmp_path: Path) -> None:
    ws_root = tmp_path / "project"
    ws_root.mkdir()
    ws_tools = [
        {"name": "ws_tool", "input_schema": {}, "cyt_catalog_scope": "workspace"},
        {"name": "user_tool", "input_schema": {}, "cyt_catalog_scope": "user"},
    ]
    _register_workspace(ws_root, ws_tools)

    merged = catalog_for_hook("cursor", ws_root)
    assert {tool["name"] for tool in merged} == {"ws_tool", "user_tool"}
    assert merge_catalog_for_hook("cursor", ws_root) == merged
    assert catalog_for_hook("cursor", None) == []
    assert catalog_for_hook("cursor", tmp_path / "other") == []


def test_hash_only_heartbeat_returns_unchanged(tmp_path: Path) -> None:
    ws_root = tmp_path / "project"
    ws_root.mkdir()
    tools = [{"name": "t1", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    _register_workspace(ws_root, tools)
    result = register_catalog(
        {
            "agent": "cursor",
            "scope": "workspace",
            "workspace_root": str(ws_root),
            "instance_id": "pid:1",
            "content_hash": content_hash,
        },
    )
    assert result.status == RegisterStatus.UNCHANGED
    assert result.http_status == 204


def test_hash_only_unknown_returns_404(tmp_path: Path) -> None:
    ws_root = tmp_path / "project"
    ws_root.mkdir()
    result = register_catalog(
        {
            "agent": "cursor",
            "scope": "workspace",
            "workspace_root": str(ws_root),
            "instance_id": "pid:1",
            "content_hash": "missing",
        },
    )
    assert result.status == RegisterStatus.UNKNOWN_HASH
    assert result.http_status == 404


def test_touch_heartbeat_records_unchanged(tmp_path: Path) -> None:
    ws_root = tmp_path / "project"
    ws_root.mkdir()
    tools = [{"name": "t1", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    _register_workspace(ws_root, tools)
    result = touch_heartbeat(
        "cursor",
        "workspace",
        ws_root,
        content_hash=content_hash,
        instance_id="pid:1",
    )
    assert result.status == RegisterStatus.UNCHANGED
    assert result.http_status == 204


def test_ttl_expiry_excludes_live_lookup_but_keeps_stale_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws_root = tmp_path / "project"
    ws_root.mkdir()
    tools = [{"name": "ttl_tool", "input_schema": {}}]
    now = {"value": 1000.0}
    monkeypatch.setattr("cyt.hook.catalog_registry.time.monotonic", lambda: now["value"])
    _register_workspace(ws_root, tools)

    now["value"] += 11.0
    assert catalog_for_hook("cursor", ws_root, allow_stale=False) == []
    assert catalog_for_hook("cursor", ws_root, allow_stale=True) != []

    removed = prune_expired_registrations()
    assert removed == 1
    assert catalog_for_hook("cursor", ws_root, allow_stale=True) == []


def test_daemon_restart_stale_snapshot_repushed_clears_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws_root = tmp_path / "project"
    ws_root.mkdir()
    snapshot_dir = tmp_path / "catalog-registry"
    snapshot_dir.mkdir()
    snapshot_file = snapshot_dir / "registrations.json"
    tools = [{"name": "restart_tool", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    snapshot_file.write_text(
        json.dumps(
            [
                {
                    "agent": "cursor",
                    "scope": "workspace",
                    "workspace_root": str(ws_root.resolve()),
                    "tools": tools,
                    "content_hash": content_hash,
                    "instance_id": "pid:old",
                    "registered_at": 1.0,
                    "last_seen_at": 1.0,
                    "stale": False,
                },
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("cyt.hook.catalog_registry.REGISTRY_SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr("cyt.hook.catalog_registry.REGISTRY_SNAPSHOT_FILE", snapshot_file)

    loaded = load_catalog_registry_from_disk(mark_stale=True)
    assert loaded == 1
    merged = catalog_for_hook("cursor", ws_root, allow_stale=True)
    assert {tool["name"] for tool in merged} == {"restart_tool"}

    result = register_catalog(
        {
            "agent": "cursor",
            "scope": "workspace",
            "workspace_root": str(ws_root),
            "instance_id": "pid:new",
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    assert result.status == RegisterStatus.STORED
    assert catalog_for_hook("cursor", ws_root, allow_stale=False) != []


def test_disk_snapshot_drops_legacy_global_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_dir = tmp_path / "catalog-registry"
    snapshot_dir.mkdir()
    snapshot_file = snapshot_dir / "registrations.json"
    tools = [{"name": "legacy_tool", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    snapshot_file.write_text(
        json.dumps(
            [
                {
                    "agent": "cursor",
                    "scope": "global",
                    "workspace_root": None,
                    "tools": tools,
                    "content_hash": content_hash,
                    "instance_id": "pid:old",
                    "registered_at": 1.0,
                    "last_seen_at": 1.0,
                    "stale": False,
                },
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("cyt.hook.catalog_registry.REGISTRY_SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr("cyt.hook.catalog_registry.REGISTRY_SNAPSHOT_FILE", snapshot_file)

    assert load_catalog_registry_from_disk(mark_stale=True) == 0

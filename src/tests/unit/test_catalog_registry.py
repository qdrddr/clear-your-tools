"""Tests for cyt-mcp push catalog registry."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.cyt_mcp.catalog_disk import raw_catalog_content_hash
from cyt.hook.catalog_registry import (
    RegisterStatus,
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


def test_register_and_merge_global_workspace(tmp_path: Path) -> None:
    ws_root = tmp_path / "project"
    ws_root.mkdir()
    global_tools = [{"name": "global_tool", "input_schema": {}}]
    ws_tools = [
        {"name": "global_tool", "input_schema": {"type": "object", "properties": {}}},
        {"name": "ws_tool", "input_schema": {}},
    ]
    global_hash = raw_catalog_content_hash(global_tools)
    ws_hash = raw_catalog_content_hash(ws_tools)

    global_result = register_catalog(
        {
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": global_hash,
            "tools": global_tools,
        },
    )
    assert global_result.status == RegisterStatus.STORED

    ws_result = register_catalog(
        {
            "agent": "cursor",
            "scope": "workspace",
            "workspace_root": str(ws_root),
            "instance_id": "pid:2",
            "content_hash": ws_hash,
            "tools": ws_tools,
        },
    )
    assert ws_result.status == RegisterStatus.STORED

    merged = merge_catalog_for_hook("cursor", ws_root)
    names = {tool["name"] for tool in merged}
    assert names == {"global_tool", "ws_tool"}
    ws_tool = next(tool for tool in merged if tool["name"] == "global_tool")
    assert ws_tool["input_schema"] == {"type": "object", "properties": {}}
    assert ws_tool["cyt_catalog_scope"] == "workspace"
    ws_only = next(tool for tool in merged if tool["name"] == "ws_tool")
    assert ws_only["cyt_catalog_scope"] == "workspace"
    user_only = merge_catalog_for_hook("cursor", None)
    assert user_only[0]["cyt_catalog_scope"] == "user"


def test_hash_only_heartbeat_returns_unchanged() -> None:
    tools = [{"name": "t1", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    register_catalog(
        {
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    result = register_catalog(
        {
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": content_hash,
        },
    )
    assert result.status == RegisterStatus.UNCHANGED
    assert result.http_status == 204


def test_hash_only_unknown_returns_404() -> None:
    result = register_catalog(
        {
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": "missing",
        },
    )
    assert result.status == RegisterStatus.UNKNOWN_HASH
    assert result.http_status == 404


def test_touch_heartbeat_records_unchanged() -> None:
    tools = [{"name": "t1", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    register_catalog(
        {
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    result = touch_heartbeat(
        "cursor",
        "global",
        None,
        content_hash=content_hash,
        instance_id="pid:1",
    )
    assert result.status == RegisterStatus.UNCHANGED
    assert result.http_status == 204


def test_ttl_expiry_excludes_live_merge_but_keeps_stale_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [{"name": "ttl_tool", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    now = {"value": 1000.0}
    monkeypatch.setattr("cyt.hook.catalog_registry.time.monotonic", lambda: now["value"])
    register_catalog(
        {
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": content_hash,
            "tools": tools,
        },
    )

    now["value"] += 11.0
    assert merge_catalog_for_hook("cursor", None, allow_stale=False) == []
    assert merge_catalog_for_hook("cursor", None, allow_stale=True) != []

    removed = prune_expired_registrations()
    assert removed == 1
    assert merge_catalog_for_hook("cursor", None, allow_stale=True) == []


def test_daemon_restart_stale_snapshot_repushed_clears_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    loaded = load_catalog_registry_from_disk(mark_stale=True)
    assert loaded == 1
    merged = merge_catalog_for_hook("cursor", None, allow_stale=True)
    assert {tool["name"] for tool in merged} == {"restart_tool"}

    result = register_catalog(
        {
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:new",
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    assert result.status == RegisterStatus.STORED
    assert merge_catalog_for_hook("cursor", None, allow_stale=False) != []

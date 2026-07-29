"""Tests for CYT runtime registries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt import runtime_registry


@pytest.fixture
def registry_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    hook_path = tmp_path / "hook-daemon.json"
    proxy_path = tmp_path / "proxy.json"
    monkeypatch.setattr(runtime_registry, "HOOK_DAEMON_REGISTRY", hook_path)
    monkeypatch.setattr(runtime_registry, "HOOK_DAEMON_PIDFILE", hook_path)
    monkeypatch.setattr(runtime_registry, "PROXY_REGISTRY", proxy_path)
    return hook_path, proxy_path


def test_hook_daemon_registry_stores_array(registry_paths: tuple[Path, Path]) -> None:
    hook_path, _ = registry_paths
    runtime_registry.upsert_hook_daemon_entry(
        port=8834,
        hook_url="http://127.0.0.1:8834/hook/inject",
        pid=111,
        reused=False,
        mode="hooks_only",
    )
    runtime_registry.upsert_hook_daemon_entry(
        port=8835,
        hook_url="http://127.0.0.1:8835/hook/inject",
        pid=222,
        reused=False,
        mode="full_proxy",
    )

    payload = json.loads(hook_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert [entry["port"] for entry in payload] == [8834, 8835]
    entry = runtime_registry.find_hook_daemon_entry_for_port(8835)
    assert entry is not None
    assert entry["pid"] == 222


def test_legacy_hook_daemon_object_is_migrated_on_read(registry_paths: tuple[Path, Path]) -> None:
    hook_path, _ = registry_paths
    hook_path.write_text(
        json.dumps({"pid": 111, "port": 8834, "hook_url": "http://127.0.0.1:8834/hook/inject"}),
        encoding="utf-8",
    )

    entries = runtime_registry.read_hook_daemon_entries()
    assert len(entries) == 1
    assert entries[0]["port"] == 8834
    assert runtime_registry.read_hook_daemon_pidfile() == entries[0]


def test_proxy_registry_tracks_multiple_proxies(registry_paths: tuple[Path, Path]) -> None:
    _, proxy_path = registry_paths
    runtime_registry.upsert_proxy_entry(port=8834, pid=111, owner="cyt-proxy")
    runtime_registry.upsert_proxy_entry(port=8840, pid=222, owner="cyt-launch")

    payload = json.loads(proxy_path.read_text(encoding="utf-8"))
    assert [entry["port"] for entry in payload] == [8834, 8840]

    runtime_registry.remove_proxy_entries(ports={8834})
    remaining = runtime_registry.read_proxy_entries()
    assert [entry["port"] for entry in remaining] == [8840]


def test_empty_registry_removes_file(registry_paths: tuple[Path, Path]) -> None:
    hook_path, _ = registry_paths
    runtime_registry.upsert_hook_daemon_entry(
        port=8834,
        hook_url="http://127.0.0.1:8834/hook/inject",
        pid=111,
        reused=False,
        mode="hooks_only",
    )
    runtime_registry.remove_hook_daemon_entries()
    assert not hook_path.exists()

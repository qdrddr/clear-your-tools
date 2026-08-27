"""Tests for CYT runtime registries."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt import runtime_registry


@pytest.fixture
def registry_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    pid_path = tmp_path / "pid.json"
    legacy_hook_path = tmp_path / "hook-daemon.json"
    legacy_proxy_path = tmp_path / "proxy.json"
    monkeypatch.setattr(runtime_registry, "PID_REGISTRY", pid_path)
    monkeypatch.setattr(runtime_registry, "HOOK_DAEMON_REGISTRY", pid_path)
    monkeypatch.setattr(runtime_registry, "PROXY_REGISTRY", pid_path)
    monkeypatch.setattr(runtime_registry, "HOOK_DAEMON_PIDFILE", pid_path)
    monkeypatch.setattr(runtime_registry, "LEGACY_HOOK_DAEMON_REGISTRY", legacy_hook_path)
    monkeypatch.setattr(runtime_registry, "LEGACY_PROXY_REGISTRY", legacy_proxy_path)
    return pid_path, legacy_hook_path, legacy_proxy_path


def test_hook_daemon_registry_stores_array(registry_paths: tuple[Path, Path, Path]) -> None:
    pid_path, _, _ = registry_paths
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

    payload = json.loads(pid_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert [entry["port"] for entry in payload] == [8834, 8835]
    assert all(entry["owner"] == runtime_registry.OWNER_HOOK_DAEMON for entry in payload)
    entry = runtime_registry.find_hook_daemon_entry_for_port(8835)
    assert entry is not None
    assert entry["pid"] == 222


def test_legacy_hook_daemon_object_is_migrated_on_read(
    registry_paths: tuple[Path, Path, Path],
) -> None:
    _, legacy_hook_path, _ = registry_paths
    legacy_hook_path.write_text(
        json.dumps({"pid": 111, "port": 8834, "hook_url": "http://127.0.0.1:8834/hook/inject"}),
        encoding="utf-8",
    )

    entries = runtime_registry.read_hook_daemon_entries()
    assert len(entries) == 1
    assert entries[0]["port"] == 8834
    assert entries[0]["owner"] == runtime_registry.OWNER_HOOK_DAEMON
    assert runtime_registry.read_hook_daemon_pidfile() == entries[0]


def test_proxy_registry_tracks_multiple_proxies(registry_paths: tuple[Path, Path, Path]) -> None:
    pid_path, _, _ = registry_paths
    runtime_registry.upsert_proxy_entry(port=8834, pid=111, owner="cyt-proxy")
    runtime_registry.upsert_proxy_entry(port=8840, pid=222, owner="cyt-launch")

    payload = json.loads(pid_path.read_text(encoding="utf-8"))
    assert [entry["port"] for entry in payload] == [8834, 8840]
    assert {entry["owner"] for entry in payload} == {"cyt-proxy", "cyt-launch"}
    assert runtime_registry.read_hook_daemon_entries() == []

    runtime_registry.remove_proxy_entries(ports={8834})
    remaining = runtime_registry.read_proxy_entries(prune=False)
    assert [entry["port"] for entry in remaining] == [8840]


def test_upsert_replaces_conflicting_owner_on_same_port(
    registry_paths: tuple[Path, Path, Path],
) -> None:
    pid_path, _, _ = registry_paths
    runtime_registry.upsert_proxy_entry(port=8835, pid=111, owner="cyt-proxy")
    runtime_registry.upsert_hook_daemon_entry(
        port=8835,
        hook_url="http://127.0.0.1:8835/hook/inject",
        pid=222,
        reused=False,
        mode="hooks_only",
    )

    payload = json.loads(pid_path.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["owner"] == runtime_registry.OWNER_HOOK_DAEMON
    assert payload[0]["pid"] == 222
    assert runtime_registry.read_proxy_entries(prune=False) == []


def test_legacy_registries_merge_into_pid_json(registry_paths: tuple[Path, Path, Path]) -> None:
    pid_path, legacy_hook_path, legacy_proxy_path = registry_paths
    legacy_proxy_path.write_text(
        json.dumps([{"pid": 66292, "port": 8834, "owner": "cyt-proxy"}]),
        encoding="utf-8",
    )
    legacy_hook_path.write_text(
        json.dumps(
            [
                {
                    "pid": 69783,
                    "port": 8835,
                    "hook_url": "http://127.0.0.1:8835/hook/inject",
                    "mode": "hooks_only",
                    "owner": "cyt-hook-daemon",
                },
            ],
        ),
        encoding="utf-8",
    )

    entries = runtime_registry.read_runtime_entries()
    assert {entry["port"] for entry in entries} == {8834, 8835}
    assert not legacy_hook_path.exists()
    assert not legacy_proxy_path.exists()
    assert pid_path.is_file()


def test_legacy_overlap_prefers_hook_daemon_owner(registry_paths: tuple[Path, Path, Path]) -> None:
    pid_path, legacy_hook_path, legacy_proxy_path = registry_paths
    legacy_proxy_path.write_text(
        json.dumps([{"pid": 69783, "port": 8835, "owner": "cyt-proxy"}]),
        encoding="utf-8",
    )
    legacy_hook_path.write_text(
        json.dumps(
            [
                {
                    "pid": 69783,
                    "port": 8835,
                    "hook_url": "http://127.0.0.1:8835/hook/inject",
                    "mode": "hooks_only",
                    "owner": "cyt-hook-daemon",
                },
            ],
        ),
        encoding="utf-8",
    )

    runtime_registry.read_runtime_entries()
    payload = json.loads(pid_path.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["owner"] == runtime_registry.OWNER_HOOK_DAEMON


def test_empty_registry_removes_file(registry_paths: tuple[Path, Path, Path]) -> None:
    pid_path, _, _ = registry_paths
    runtime_registry.upsert_hook_daemon_entry(
        port=8834,
        hook_url="http://127.0.0.1:8834/hook/inject",
        pid=111,
        reused=False,
        mode="hooks_only",
    )
    runtime_registry.remove_hook_daemon_entries()
    assert not pid_path.exists()


def test_read_runtime_entries_prunes_dead_records(registry_paths: tuple[Path, Path, Path]) -> None:
    pid_path, _, _ = registry_paths
    pid_path.write_text(
        json.dumps(
            [
                {"pid": 999999, "port": 8834, "owner": "cyt-proxy"},
                {"pid": 888888, "port": 8835, "owner": "cyt-proxy"},
            ],
        ),
        encoding="utf-8",
    )

    with (
        patch("cyt.runtime_registry._runtime_entry_is_live", side_effect=[False, True]),
        patch(
            "cyt.runtime_registry._refresh_runtime_entry",
            side_effect=[None, {"pid": 42, "port": 8835, "owner": "cyt-proxy"}],
        ),
    ):
        entries = runtime_registry.read_runtime_entries(prune=True)

    assert entries == [{"pid": 42, "port": 8835, "owner": "cyt-proxy"}]
    payload = json.loads(pid_path.read_text(encoding="utf-8"))
    assert payload == [{"pid": 42, "port": 8835, "owner": "cyt-proxy"}]


def test_remove_proxy_entry_does_not_drop_hook_daemon(
    registry_paths: tuple[Path, Path, Path],
) -> None:
    pid_path, _, _ = registry_paths
    runtime_registry.upsert_hook_daemon_entry(
        port=8835,
        hook_url="http://127.0.0.1:8835/hook/inject",
        pid=222,
        reused=False,
        mode="hooks_only",
    )
    runtime_registry.remove_proxy_entries(ports={8835})

    remaining = runtime_registry.read_runtime_entries(prune=False)
    assert len(remaining) == 1
    assert remaining[0]["owner"] == runtime_registry.OWNER_HOOK_DAEMON
    assert json.loads(pid_path.read_text(encoding="utf-8"))[0]["port"] == 8835


def test_hook_daemon_reuse_preserves_started_at(registry_paths: tuple[Path, Path, Path]) -> None:
    pid_path, _, _ = registry_paths
    runtime_registry.upsert_hook_daemon_entry(
        port=8834,
        hook_url="http://127.0.0.1:8834/hook/inject",
        pid=111,
        reused=False,
        mode="hooks_only",
    )
    entry = runtime_registry.find_hook_daemon_entry_for_port(8834)
    if entry is None:
        pytest.fail("expected hook daemon entry for port 8834")
    started_at = entry["started_at"]

    runtime_registry.upsert_hook_daemon_entry(
        port=8834,
        hook_url="http://127.0.0.1:8834/hook/inject",
        pid=None,
        reused=True,
        mode="hooks_only",
    )

    payload = json.loads(pid_path.read_text(encoding="utf-8"))
    assert payload[0]["started_at"] == started_at
    assert payload[0]["reused"] is True

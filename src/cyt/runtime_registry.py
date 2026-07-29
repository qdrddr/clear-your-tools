"""Persistent registries for running CYT hook daemons and reverse proxies."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HOOK_DAEMON_REGISTRY = Path("~/.config/cyt/hook-daemon.json").expanduser()
PROXY_REGISTRY = Path("~/.config/cyt/proxy.json").expanduser()

# Backward-compatible alias used across the codebase.
HOOK_DAEMON_PIDFILE = HOOK_DAEMON_REGISTRY


def _read_registry(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return []
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _write_registry(path: Path, entries: list[dict[str, Any]]) -> None:
    if not entries:
        if path.is_file():
            path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def _upsert_by_port(entries: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    port = entry.get("port")
    filtered = [existing for existing in entries if existing.get("port") != port]
    filtered.append(entry)
    return filtered


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def read_hook_daemon_entries() -> list[dict[str, Any]]:
    return _read_registry(HOOK_DAEMON_REGISTRY)


def read_hook_daemon_pidfile() -> dict[str, Any] | None:
    """Return the most recently recorded hook daemon entry, if any."""
    entries = read_hook_daemon_entries()
    if not entries:
        return None
    return entries[-1]


def upsert_hook_daemon_entry(
    *,
    port: int,
    hook_url: str,
    pid: int | None,
    reused: bool,
    mode: str,
    credentials_injected: bool = False,
) -> None:
    entry = {
        "pid": pid,
        "port": port,
        "hook_url": hook_url,
        "mode": mode,
        "owner": "cyt-hook-daemon",
        "started_at": _now_iso(),
        "reused": reused,
        "credentials_injected": credentials_injected,
    }
    entries = _upsert_by_port(read_hook_daemon_entries(), entry)
    _write_registry(HOOK_DAEMON_REGISTRY, entries)


def remove_hook_daemon_entries(*, ports: set[int] | None = None) -> None:
    entries = read_hook_daemon_entries()
    if ports is None:
        _write_registry(HOOK_DAEMON_REGISTRY, [])
        return
    remaining = [entry for entry in entries if entry.get("port") not in ports]
    _write_registry(HOOK_DAEMON_REGISTRY, remaining)


def find_hook_daemon_entry_for_port(port: int) -> dict[str, Any] | None:
    for entry in reversed(read_hook_daemon_entries()):
        if entry.get("port") == port:
            return entry
    return None


def read_proxy_entries() -> list[dict[str, Any]]:
    return _read_registry(PROXY_REGISTRY)


def upsert_proxy_entry(
    *,
    port: int,
    pid: int,
    owner: str,
    config_path: Path | None = None,
    credentials_injected: bool = False,
    debug: bool = False,
    debug_dry_run: bool = False,
) -> None:
    entry: dict[str, Any] = {
        "pid": pid,
        "port": port,
        "owner": owner,
        "started_at": _now_iso(),
        "credentials_injected": credentials_injected,
        "debug": debug,
        "debug_dry_run": debug_dry_run,
    }
    if config_path is not None:
        entry["config_path"] = str(config_path)
    entries = _upsert_by_port(read_proxy_entries(), entry)
    _write_registry(PROXY_REGISTRY, entries)


def remove_proxy_entries(*, ports: set[int] | None = None) -> None:
    entries = read_proxy_entries()
    if ports is None:
        _write_registry(PROXY_REGISTRY, [])
        return
    remaining = [entry for entry in entries if entry.get("port") not in ports]
    _write_registry(PROXY_REGISTRY, remaining)

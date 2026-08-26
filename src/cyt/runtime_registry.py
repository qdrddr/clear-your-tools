"""Persistent registry for running CYT hook daemons and reverse proxies."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

RuntimeOwner = Literal["cyt-hook-daemon", "cyt-proxy", "cyt-launch"]

OWNER_HOOK_DAEMON: RuntimeOwner = "cyt-hook-daemon"
OWNER_PROXY: RuntimeOwner = "cyt-proxy"
OWNER_LAUNCH: RuntimeOwner = "cyt-launch"
PROXY_OWNERS: frozenset[RuntimeOwner] = frozenset({OWNER_PROXY, OWNER_LAUNCH})

PID_REGISTRY = Path("~/.config/cyt/pid.json").expanduser()
LEGACY_HOOK_DAEMON_REGISTRY = Path("~/.config/cyt/hook-daemon.json").expanduser()
LEGACY_PROXY_REGISTRY = Path("~/.config/cyt/proxy.json").expanduser()

# Backward-compatible aliases used across the codebase.
HOOK_DAEMON_REGISTRY = PID_REGISTRY
PROXY_REGISTRY = PID_REGISTRY
HOOK_DAEMON_PIDFILE = PID_REGISTRY


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


def _normalize_owner(entry: dict[str, Any]) -> str:
    owner = entry.get("owner")
    if isinstance(owner, str) and owner:
        return owner
    if entry.get("hook_url") is not None or entry.get("mode") is not None or entry.get("reused"):
        return OWNER_HOOK_DAEMON
    return OWNER_PROXY


def _merge_legacy_entries(
    hook_entries: list[dict[str, Any]],
    proxy_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge legacy registries into one entry per port with a single owner."""
    by_port: dict[int, dict[str, Any]] = {}

    for entry in proxy_entries:
        port = entry.get("port")
        if isinstance(port, int):
            normalized = dict(entry)
            normalized["owner"] = _normalize_owner(normalized)
            by_port[port] = normalized

    for entry in hook_entries:
        port = entry.get("port")
        if not isinstance(port, int):
            continue
        normalized = dict(entry)
        normalized["owner"] = OWNER_HOOK_DAEMON
        existing = by_port.get(port)
        if existing is None:
            by_port[port] = normalized
            continue
        merged = dict(existing)
        merged.update(normalized)
        merged["owner"] = OWNER_HOOK_DAEMON
        by_port[port] = merged

    return sorted(by_port.values(), key=lambda item: int(item.get("port", 0)))


def _migrate_legacy_registries() -> None:
    hook_entries = _read_registry(LEGACY_HOOK_DAEMON_REGISTRY)
    proxy_entries = _read_registry(LEGACY_PROXY_REGISTRY)
    if not hook_entries and not proxy_entries:
        return

    merged = _merge_legacy_entries(hook_entries, proxy_entries)
    current = _read_registry(PID_REGISTRY)
    if not current:
        _write_registry(PID_REGISTRY, merged)
    for legacy_path in (LEGACY_HOOK_DAEMON_REGISTRY, LEGACY_PROXY_REGISTRY):
        if legacy_path.is_file():
            legacy_path.unlink(missing_ok=True)


def read_runtime_entries(*, prune: bool = False) -> list[dict[str, Any]]:
    _migrate_legacy_registries()
    entries = _read_registry(PID_REGISTRY)
    if not prune or not entries:
        return entries
    return prune_stale_runtime_entries(entries)


def read_hook_daemon_entries() -> list[dict[str, Any]]:
    return [
        entry for entry in read_runtime_entries() if _normalize_owner(entry) == OWNER_HOOK_DAEMON
    ]


def read_hook_daemon_pidfile() -> dict[str, Any] | None:
    """Return the most recently recorded hook daemon entry, if any."""
    entries = read_hook_daemon_entries()
    if not entries:
        return None
    return entries[-1]


def read_proxy_entries(*, prune: bool = True) -> list[dict[str, Any]]:
    entries = read_runtime_entries(prune=prune)
    return [entry for entry in entries if _normalize_owner(entry) in PROXY_OWNERS]


def find_hook_daemon_entry_for_port(port: int) -> dict[str, Any] | None:
    for entry in reversed(read_hook_daemon_entries()):
        if entry.get("port") == port:
            return entry
    return None


def find_runtime_entry_for_port(port: int) -> dict[str, Any] | None:
    for entry in reversed(read_runtime_entries()):
        if entry.get("port") == port:
            return entry
    return None


def _runtime_entry_is_live(entry: dict[str, Any]) -> bool:
    from cyt.hook.daemon import _find_listen_pid, _pid_alive
    from cyt.hook.port import fetch_cyt_health
    from cyt.launch.proxy_guard import _is_cyt_health

    port = entry.get("port")
    pid = entry.get("pid")
    if isinstance(pid, int) and _pid_alive(pid):
        return True
    if isinstance(port, int):
        if _is_cyt_health(fetch_cyt_health(port)):
            return _find_listen_pid(port) is not None
    return False


def _refresh_runtime_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Return an updated live entry, or ``None`` when it should be dropped."""
    from cyt.hook.daemon import _find_listen_pid, _pid_alive

    if not _runtime_entry_is_live(entry):
        return None

    port = entry.get("port")
    if not isinstance(port, int):
        return entry

    listener = _find_listen_pid(port)
    if listener is None:
        return entry

    refreshed = dict(entry)
    refreshed["owner"] = _normalize_owner(refreshed)
    refreshed["pid"] = listener
    pid = entry.get("pid")
    if isinstance(pid, int) and _pid_alive(pid) and pid != listener:
        return refreshed
    if pid != listener:
        return refreshed
    return entry


def prune_stale_runtime_entries(
    entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Drop dead runtime records and refresh PIDs for live listeners."""
    _migrate_legacy_registries()
    current = _read_registry(PID_REGISTRY) if entries is None else entries
    refreshed: list[dict[str, Any]] = []
    for entry in current:
        updated = _refresh_runtime_entry(entry)
        if updated is not None:
            refreshed.append(updated)
    if refreshed != current:
        _write_registry(PID_REGISTRY, refreshed)
    return refreshed


def prune_stale_proxy_entries(
    entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Backward-compatible alias for proxy entry pruning."""
    del entries
    prune_stale_runtime_entries()
    return read_proxy_entries(prune=False)


def upsert_runtime_entry(entry: dict[str, Any]) -> None:
    normalized = dict(entry)
    normalized["owner"] = _normalize_owner(normalized)
    if "started_at" not in normalized:
        normalized["started_at"] = _now_iso()
    entries = _upsert_by_port(read_runtime_entries(prune=False), normalized)
    _write_registry(PID_REGISTRY, entries)


def upsert_hook_daemon_entry(
    *,
    port: int,
    hook_url: str,
    pid: int | None,
    reused: bool,
    mode: str,
    credentials_injected: bool = False,
) -> None:
    existing = find_hook_daemon_entry_for_port(port)
    started_at = _now_iso()
    if reused and existing is not None:
        existing_started_at = existing.get("started_at")
        if isinstance(existing_started_at, str) and existing_started_at:
            started_at = existing_started_at
    upsert_runtime_entry(
        {
            "pid": pid,
            "port": port,
            "hook_url": hook_url,
            "mode": mode,
            "owner": OWNER_HOOK_DAEMON,
            "started_at": started_at,
            "reused": reused,
            "credentials_injected": credentials_injected,
        },
    )


def upsert_proxy_entry(
    *,
    port: int,
    pid: int,
    owner: RuntimeOwner | str,
    config_path: Path | None = None,
    credentials_injected: bool = False,
    debug: bool = False,
    debug_dry_run: bool = False,
) -> None:
    if owner not in PROXY_OWNERS:
        raise ValueError(f"unsupported proxy owner: {owner}")
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
    upsert_runtime_entry(entry)


def _remove_runtime_entries(
    *,
    ports: set[int] | None = None,
    owners: set[str] | None = None,
) -> None:
    _migrate_legacy_registries()
    entries = read_runtime_entries(prune=False)

    def should_remove(entry: dict[str, Any]) -> bool:
        port = entry.get("port")
        owner = _normalize_owner(entry)
        if ports is not None:
            if not isinstance(port, int) or port not in ports:
                return False
            return owners is None or owner in owners
        if owners is not None:
            return owner in owners
        return True

    remaining = [entry for entry in entries if not should_remove(entry)]
    _write_registry(PID_REGISTRY, remaining)


def remove_hook_daemon_entries(*, ports: set[int] | None = None) -> None:
    if ports is None:
        _remove_runtime_entries(owners={OWNER_HOOK_DAEMON})
        return
    _remove_runtime_entries(ports=ports, owners={OWNER_HOOK_DAEMON})


def remove_proxy_entries(*, ports: set[int] | None = None) -> None:
    if ports is None:
        _remove_runtime_entries(owners=set(PROXY_OWNERS))
        return
    _remove_runtime_entries(ports=ports, owners=set(PROXY_OWNERS))

"""Port discovery for cyt-client (stdlib only; no cyt imports)."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 8834
HEALTH_TIMEOUT_SECONDS = 1.5
HEALTH_TTL_SECONDS = 30.0
HOOK_CONNECT_PATH = "/hook/connect"
HOOK_INJECT_PATH = HOOK_CONNECT_PATH  # backward-compatible alias
CYT_HOOK_URL_ENV = "CYT_HOOK_URL"
HOOK_DAEMON_PIDFILE = Path("~/.config/cyt/pid.json").expanduser()
LEGACY_HOOK_DAEMON_PIDFILE = Path("~/.config/cyt/hook-daemon.json").expanduser()
OWNER_HOOK_DAEMON = "cyt-hook-daemon"

_last_live_hook_url: tuple[str, float] | None = None


def clear_hook_url_cache() -> None:
    """Drop cached hook URL health (e.g. after HTTP 5xx from inject)."""
    global _last_live_hook_url
    _last_live_hook_url = None


def hook_url_for_port(port: int) -> str:
    return f"http://{LOCAL_HOST}:{port}{HOOK_INJECT_PATH}"


def fetch_cyt_health(port: int) -> dict[str, Any] | None:
    url = f"http://{LOCAL_HOST}:{port}/health"
    try:
        with urlopen(url, timeout=HEALTH_TIMEOUT_SECONDS) as response:
            code = response.getcode()
            if not isinstance(code, int) or code != 200:
                return None
            payload = json.loads(response.read())
            return payload if isinstance(payload, dict) else None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


def is_hook_server(health: dict[str, Any] | None) -> bool:
    return (
        isinstance(health, dict)
        and health.get("name") == "cyt"
        and health.get("status") == "ok"
        and health.get("hook") is True
    )


def _read_hook_daemon_entries() -> list[dict[str, Any]]:
    for path in (HOOK_DAEMON_PIDFILE, LEGACY_HOOK_DAEMON_PIDFILE):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, list):
            entries = [entry for entry in payload if isinstance(entry, dict)]
        elif isinstance(payload, dict):
            entries = [payload]
        else:
            entries = []
        hook_entries = [
            entry
            for entry in entries
            if entry.get("owner") == OWNER_HOOK_DAEMON or entry.get("hook_url") is not None
        ]
        if hook_entries:
            return hook_entries
    return []


def read_hook_daemon_pidfile() -> dict[str, Any] | None:
    entries = _read_hook_daemon_entries()
    if not entries:
        return None
    return entries[-1]


def _port_from_hook_url(url: str) -> int | None:
    match = re.search(r":(\d+)/", url)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _base_port_from_env_or_pidfile() -> int:
    env_url = os.environ.get(CYT_HOOK_URL_ENV, "").strip()
    if env_url:
        port = _port_from_hook_url(env_url)
        if port is not None:
            return port

    for pidfile in reversed(_read_hook_daemon_entries()):
        hook_url = pidfile.get("hook_url")
        if isinstance(hook_url, str):
            port = _port_from_hook_url(hook_url)
            if port is not None:
                return port
        port_value = pidfile.get("port")
        if isinstance(port_value, int):
            return port_value
    return DEFAULT_PORT


def _hook_scan_start_ports() -> list[int]:
    """Prefer DEFAULT_PORT, then env/pidfile hint, when scanning for hook servers."""
    starts: list[int] = []
    seen: set[int] = set()
    for port in (DEFAULT_PORT, _base_port_from_env_or_pidfile()):
        if port not in seen:
            seen.add(port)
            starts.append(port)
    return starts


def find_hook_server_port(*, max_attempts: int = 100) -> int | None:
    for start in _hook_scan_start_ports():
        for attempt in range(max_attempts):
            port = start + attempt
            if is_hook_server(fetch_cyt_health(port)):
                return port
    return None


def find_hook_server_port_excluding(
    excluded_port: int | None,
    *,
    max_attempts: int = 100,
) -> int | None:
    for start in _hook_scan_start_ports():
        for attempt in range(max_attempts):
            port = start + attempt
            if excluded_port is not None and port == excluded_port:
                continue
            if is_hook_server(fetch_cyt_health(port)):
                return port
    return None


def _hook_url_is_live(url: str) -> bool:
    global _last_live_hook_url
    now = time.monotonic()
    if (
        _last_live_hook_url is not None
        and _last_live_hook_url[0] == url
        and now - _last_live_hook_url[1] < HEALTH_TTL_SECONDS
    ):
        return True
    port = _port_from_hook_url(url)
    if port is None:
        return False
    live = is_hook_server(fetch_cyt_health(port))
    if live:
        _last_live_hook_url = (url, now)
    return live


def resolve_hook_url() -> str | None:
    env_url = os.environ.get(CYT_HOOK_URL_ENV, "").strip()
    if env_url:
        if env_url.endswith("/hook/inject"):
            resolved = env_url[: -len("/hook/inject")] + HOOK_CONNECT_PATH
        elif env_url.endswith(HOOK_INJECT_PATH):
            resolved = env_url
        else:
            resolved = f"{env_url.rstrip('/')}{HOOK_CONNECT_PATH}"
        if _hook_url_is_live(resolved):
            return resolved

    for pidfile in reversed(_read_hook_daemon_entries()):
        hook_url = pidfile.get("hook_url")
        if isinstance(hook_url, str) and hook_url.strip():
            resolved = hook_url.strip()
            if resolved.endswith("/hook/inject"):
                resolved = resolved[: -len("/hook/inject")] + HOOK_CONNECT_PATH
            if _hook_url_is_live(resolved):
                return resolved

    port = find_hook_server_port()
    if port is not None:
        return hook_url_for_port(port)
    return None

"""Port discovery for cyt-client (stdlib only; no cyt imports)."""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 8834
HEALTH_TIMEOUT_SECONDS = 1.5
HEALTH_TTL_SECONDS = 30.0
HOOK_PORT_PROBE_BATCH_SIZE = 20
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


def _is_hook_server_on_port(port: int) -> bool:
    return is_hook_server(fetch_cyt_health(port))


def _probe_hook_ports_parallel(ports: list[int]) -> int | None:
    if not ports:
        return None
    if len(ports) == 1:
        return ports[0] if _is_hook_server_on_port(ports[0]) else None

    matches: list[int] = []
    with ThreadPoolExecutor(max_workers=len(ports)) as executor:
        futures = {executor.submit(_is_hook_server_on_port, port): port for port in ports}
        for future in as_completed(futures):
            port = futures[future]
            try:
                if future.result():
                    matches.append(port)
            except Exception:
                continue
    return min(matches) if matches else None


def _find_hook_server_in_ports(
    ports: list[int],
    *,
    batch_size: int = HOOK_PORT_PROBE_BATCH_SIZE,
) -> int | None:
    for batch_start in range(0, len(ports), batch_size):
        end = batch_start + batch_size
        batch = ports[batch_start:end]
        match = _probe_hook_ports_parallel(batch)
        if match is not None:
            return match
    return None


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


def _scan_start_ports_for_hook_server(
    start: int,
    *,
    max_attempts: int,
    excluded_port: int | None = None,
) -> int | None:
    ports = [
        start + offset
        for offset in range(max_attempts)
        if excluded_port is None or start + offset != excluded_port
    ]
    return _find_hook_server_in_ports(ports)


def find_hook_server_port(*, max_attempts: int = 100) -> int | None:
    for start in _hook_scan_start_ports():
        match = _scan_start_ports_for_hook_server(start, max_attempts=max_attempts)
        if match is not None:
            return match
    return None


def find_hook_server_port_excluding(
    excluded_port: int | None,
    *,
    max_attempts: int = 100,
) -> int | None:
    for start in _hook_scan_start_ports():
        match = _scan_start_ports_for_hook_server(
            start,
            max_attempts=max_attempts,
            excluded_port=excluded_port,
        )
        if match is not None:
            return match
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


_LEGACY_HOOK_INJECT_SUFFIX = "/hook/inject"


def _normalize_hook_connect_url(url: str) -> str:
    stripped = url.strip()
    if stripped.endswith(_LEGACY_HOOK_INJECT_SUFFIX):
        return stripped[: -len(_LEGACY_HOOK_INJECT_SUFFIX)] + HOOK_CONNECT_PATH
    if stripped.endswith(HOOK_INJECT_PATH):
        return stripped
    return f"{stripped.rstrip('/')}{HOOK_CONNECT_PATH}"


def _resolve_env_hook_url() -> str | None:
    env_url = os.environ.get(CYT_HOOK_URL_ENV, "").strip()
    if not env_url:
        return None
    resolved = _normalize_hook_connect_url(env_url)
    if _hook_url_is_live(resolved):
        return resolved
    return None


def _resolve_pidfile_hook_url() -> str | None:
    live_pidfile_urls: list[tuple[str, bool]] = []
    for pidfile in reversed(_read_hook_daemon_entries()):
        hook_url = pidfile.get("hook_url")
        if not isinstance(hook_url, str) or not hook_url.strip():
            continue
        resolved = _normalize_hook_connect_url(hook_url)
        if _hook_url_is_live(resolved):
            live_pidfile_urls.append((resolved, bool(pidfile.get("credentials_injected"))))
    if not live_pidfile_urls:
        return None
    for url, credentials_injected in live_pidfile_urls:
        if credentials_injected:
            return url
    return live_pidfile_urls[0][0]


def resolve_hook_url() -> str | None:
    env_resolved = _resolve_env_hook_url()
    if env_resolved is not None:
        return env_resolved
    pidfile_resolved = _resolve_pidfile_hook_url()
    if pidfile_resolved is not None:
        return pidfile_resolved
    port = find_hook_server_port()
    if port is not None:
        return hook_url_for_port(port)
    return None

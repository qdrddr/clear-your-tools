"""Port discovery for cyt-client (stdlib only; no cyt imports)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 8834
HEALTH_TIMEOUT_SECONDS = 1.5
HOOK_INJECT_PATH = "/hook/inject"
CYT_HOOK_URL_ENV = "CYT_HOOK_URL"
HOOK_DAEMON_PIDFILE = Path("~/.config/cyt/hook-daemon.json").expanduser()


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


def read_hook_daemon_pidfile() -> dict[str, Any] | None:
    path = HOOK_DAEMON_PIDFILE
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


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

    pidfile = read_hook_daemon_pidfile()
    if pidfile is not None:
        hook_url = pidfile.get("hook_url")
        if isinstance(hook_url, str):
            port = _port_from_hook_url(hook_url)
            if port is not None:
                return port
        port_value = pidfile.get("port")
        if isinstance(port_value, int):
            return port_value
    return DEFAULT_PORT


def find_hook_server_port(*, max_attempts: int = 100) -> int | None:
    start = _base_port_from_env_or_pidfile()
    for attempt in range(max_attempts):
        port = start + attempt
        if is_hook_server(fetch_cyt_health(port)):
            return port
    return None


def _hook_url_is_live(url: str) -> bool:
    port = _port_from_hook_url(url)
    if port is None:
        return False
    return is_hook_server(fetch_cyt_health(port))


def resolve_hook_url() -> str | None:
    env_url = os.environ.get(CYT_HOOK_URL_ENV, "").strip()
    if env_url:
        resolved = (
            env_url
            if env_url.endswith(HOOK_INJECT_PATH)
            else f"{env_url.rstrip('/')}{HOOK_INJECT_PATH}"
        )
        if _hook_url_is_live(resolved):
            return resolved

    pidfile = read_hook_daemon_pidfile()
    if pidfile is not None:
        hook_url = pidfile.get("hook_url")
        if isinstance(hook_url, str) and hook_url.strip():
            resolved = hook_url.strip()
            if _hook_url_is_live(resolved):
                return resolved

    port = find_hook_server_port()
    if port is not None:
        return hook_url_for_port(port)
    return None

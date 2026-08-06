"""Port discovery and health checks for the colocated hook HTTP server."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from cyt.config import DEFAULT_REVERSE_PORT, load_config, resolve_reverse_port
from cyt.launch.proxy_guard import HEALTH_TIMEOUT_SECONDS, LOCAL_HOST
from cyt.runtime_registry import (
    read_hook_daemon_entries,
)

HOOK_CONNECT_PATH = "/hook/connect"
HOOK_INJECT_PATH = HOOK_CONNECT_PATH  # backward-compatible alias
CYT_HOOK_URL_ENV = "CYT_HOOK_URL"


def hook_url_for_port(port: int) -> str:
    return f"http://{LOCAL_HOST}:{port}{HOOK_INJECT_PATH}"


def fetch_cyt_health(port: int) -> dict[str, Any] | None:
    """GET ``/health`` JSON for *port*, or ``None`` on failure."""
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


def _port_from_hook_url(url: str) -> int | None:
    match = re.search(r":(\d+)/", url)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def resolve_hook_base_port(*, config_path: Path | None = None) -> int:
    """Resolve the base port for hook server discovery."""
    env_url = os.environ.get(CYT_HOOK_URL_ENV, "").strip()
    if env_url:
        port = _port_from_hook_url(env_url)
        if port is not None:
            return port

    for pidfile in reversed(read_hook_daemon_entries()):
        hook_url = pidfile.get("hook_url")
        if isinstance(hook_url, str):
            port = _port_from_hook_url(hook_url)
            if port is not None:
                return port
        port_value = pidfile.get("port")
        if isinstance(port_value, int):
            return port_value

    try:
        config = load_config(config_path)
        return resolve_reverse_port(config, None)
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_REVERSE_PORT


def find_hook_server_port(
    base_port: int | None = None,
    *,
    max_attempts: int = 100,
) -> int | None:
    """Scan ports starting at *base_port* for a CYT server with ``hook: true``."""
    start = base_port if base_port is not None else resolve_hook_base_port()
    for attempt in range(max_attempts):
        port = start + attempt
        health = fetch_cyt_health(port)
        if is_hook_server(health):
            return port
    return None


def resolve_hook_url(*, config_path: Path | None = None) -> str | None:
    """Resolve hook inject URL from env, pidfile, or port scan."""
    env_url = os.environ.get(CYT_HOOK_URL_ENV, "").strip()
    if env_url:
        if env_url.endswith("/hook/inject"):
            return env_url[: -len("/hook/inject")] + HOOK_CONNECT_PATH
        if env_url.endswith(HOOK_INJECT_PATH):
            return env_url
        return f"{env_url.rstrip('/')}{HOOK_INJECT_PATH}"

    for pidfile in reversed(read_hook_daemon_entries()):
        hook_url = pidfile.get("hook_url")
        if isinstance(hook_url, str) and hook_url.strip():
            resolved = hook_url.strip()
            if resolved.endswith("/hook/inject"):
                return resolved[: -len("/hook/inject")] + HOOK_CONNECT_PATH
            return resolved

    base_port = resolve_hook_base_port(config_path=config_path)
    port = find_hook_server_port(base_port)
    if port is not None:
        return hook_url_for_port(port)
    return None

"""Shared localhost hook daemon URL discovery (stdlib only)."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

LOCAL_HOST = "127.0.0.1"
DEFAULT_HOOK_PORT = 8834
HEALTH_TIMEOUT_SECONDS = 1.5
HOOK_DAEMON_PIDFILE = os.path.expanduser("~/.config/cyt/pid.json")
LEGACY_HOOK_DAEMON_PIDFILE = os.path.expanduser("~/.config/cyt/hook-daemon.json")
OWNER_HOOK_DAEMON = "cyt-hook-daemon"
CYT_HOOK_URL_ENV = "CYT_HOOK_URL"


def read_hook_daemon_entries() -> list[dict[str, Any]]:
    for path in (HOOK_DAEMON_PIDFILE, LEGACY_HOOK_DAEMON_PIDFILE):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
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


def fetch_cyt_health(
    port: int,
    *,
    timeout: float = HEALTH_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    url = f"http://{LOCAL_HOST}:{port}/health"
    try:
        with urlopen(url, timeout=timeout) as response:
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


def normalize_hook_base_url(env_url: str) -> str:
    base = env_url.strip().rstrip("/")
    for suffix in (
        "/hook/connect",
        "/hook/inject",
        "/hook/catalog/register",
        "/hook/catalog/deregister",
        "/hook/tier/feedback",
        "/hook/tool-examples/record",
        "/hook/permissions/changed",
    ):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def resolve_hook_base_url() -> str | None:
    env_url = os.environ.get(CYT_HOOK_URL_ENV, "").strip()
    if env_url:
        return normalize_hook_base_url(env_url)

    entries = read_hook_daemon_entries()
    ports: list[int] = []
    for entry in entries:
        port_raw = entry.get("port")
        if port_raw is None:
            continue
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            continue
        if port > 0:
            ports.append(port)
    if not ports:
        ports = [DEFAULT_HOOK_PORT]

    for port in sorted(set(ports)):
        if is_hook_server(fetch_cyt_health(port)):
            return f"http://{LOCAL_HOST}:{port}"
    return None


def resolve_hook_path(path: str) -> str | None:
    base = resolve_hook_base_url()
    if base is None:
        return None
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{base}{suffix}"

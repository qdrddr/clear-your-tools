"""Notify hook daemon when permissions overlays change."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PERMISSIONS_CHANGED_PATH = "/hook/permissions/changed"
NOTIFY_TIMEOUT_SECONDS = 0.5
CYT_HOOK_URL_ENV = "CYT_HOOK_URL"
HOOK_DAEMON_PIDFILE = os.path.expanduser("~/.config/cyt/pid.json")
LEGACY_HOOK_DAEMON_PIDFILE = os.path.expanduser("~/.config/cyt/hook-daemon.json")
OWNER_HOOK_DAEMON = "cyt-hook-daemon"
LOCAL_HOST = "127.0.0.1"
DEFAULT_HOOK_PORT = 8834


def _read_hook_daemon_entries() -> list[dict[str, Any]]:
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


def _fetch_cyt_health(port: int) -> dict[str, Any] | None:
    url = f"http://{LOCAL_HOST}:{port}/health"
    try:
        with urlopen(url, timeout=NOTIFY_TIMEOUT_SECONDS) as response:
            code = response.getcode()
            if not isinstance(code, int) or code != 200:
                return None
            payload = json.loads(response.read())
            return payload if isinstance(payload, dict) else None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


def _is_hook_server(health: dict[str, Any] | None) -> bool:
    return (
        isinstance(health, dict)
        and health.get("name") == "cyt"
        and health.get("status") == "ok"
        and health.get("hook") is True
    )


def _permissions_changed_url_from_env() -> str | None:
    env_url = os.environ.get(CYT_HOOK_URL_ENV, "").strip()
    if not env_url:
        return None
    base = env_url.rstrip("/")
    if base.endswith(("/hook/connect", "/hook/inject")):
        base = base.rsplit("/", 2)[0]
    elif base.endswith("/hook/catalog/register"):
        base = base[: -len("/hook/catalog/register")]
    return f"{base}{PERMISSIONS_CHANGED_PATH}"


def _permissions_changed_url_from_hook_daemon() -> str | None:
    entries = _read_hook_daemon_entries()
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
        if _is_hook_server(_fetch_cyt_health(port)):
            return f"http://{LOCAL_HOST}:{port}{PERMISSIONS_CHANGED_PATH}"
    return None


def resolve_permissions_changed_url() -> str | None:
    env_url = _permissions_changed_url_from_env()
    if env_url is not None:
        return env_url
    return _permissions_changed_url_from_hook_daemon()


def notify_permissions_changed(
    *,
    workspace_root: Path | str,
    agent: str = "cursor",
) -> bool:
    """POST permissions-changed to hook daemon. Returns True when daemon acknowledged."""
    url = resolve_permissions_changed_url()
    if url is None:
        return False
    try:
        resolved = Path(workspace_root).expanduser().resolve()
    except OSError:
        return False
    if not resolved.is_dir():
        return False
    body = {
        "workspace_root": str(resolved),
        "agent": str(agent or "cursor").strip().lower() or "cursor",
    }
    request = Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=NOTIFY_TIMEOUT_SECONDS) as response:
            code = response.getcode()
            ok = isinstance(code, int) and 200 <= code < 300
            return ok
    except (HTTPError, URLError, OSError, TimeoutError):
        return False


def print_permissions_reload_notice(*, notified: bool) -> None:
    if notified:
        print("Notified hook daemon; cyt-mcp will reload permissions on next push.")
    else:
        print(
            "Restart the agent or refresh cyt-mcp for MCP catalog changes to apply "
            "(hook daemon not reachable).",
        )

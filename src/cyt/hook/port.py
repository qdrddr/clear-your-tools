"""Port discovery and health checks for the colocated hook HTTP server."""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from cyt.config import default_reverse_port, load_config, resolve_reverse_port
from cyt.launch.proxy_guard import (
    HEALTH_TIMEOUT_SECONDS,
    LOCAL_HOST,
    find_available_port,
    is_port_in_use,
)
from cyt.runtime_registry import (
    read_hook_daemon_entries,
)

HOOK_CONNECT_PATH = "/hook/connect"
HOOK_INJECT_PATH = HOOK_CONNECT_PATH  # backward-compatible alias
CYT_HOOK_URL_ENV = "CYT_HOOK_URL"
STATUS_HEALTH_TIMEOUT_SECONDS = 0.3
HOOK_PORT_PROBE_BATCH_SIZE = 20


def hook_url_for_port(port: int) -> str:
    return f"http://{LOCAL_HOST}:{port}{HOOK_INJECT_PATH}"


def fetch_cyt_health(port: int, *, timeout: float | None = None) -> dict[str, Any] | None:
    """GET ``/health`` JSON for *port*, or ``None`` on failure."""
    url = f"http://{LOCAL_HOST}:{port}/health"
    health_timeout = HEALTH_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        with urlopen(url, timeout=health_timeout) as response:
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


def _is_hook_server_on_port(port: int, *, timeout: float | None = None) -> bool:
    return is_hook_server(fetch_cyt_health(port, timeout=timeout))


def _probe_hook_ports_parallel(
    ports: list[int],
    *,
    timeout: float | None = None,
) -> int | None:
    """Return the lowest port in *ports* that runs a hook server."""
    if not ports:
        return None
    if len(ports) == 1:
        return ports[0] if _is_hook_server_on_port(ports[0], timeout=timeout) else None

    matches: list[int] = []
    with ThreadPoolExecutor(max_workers=len(ports)) as executor:
        futures = {
            executor.submit(_is_hook_server_on_port, port, timeout=timeout): port for port in ports
        }
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
    timeout: float | None = None,
    batch_size: int = HOOK_PORT_PROBE_BATCH_SIZE,
) -> int | None:
    """Probe *ports* in parallel batches and return the lowest matching port."""
    for batch_start in range(0, len(ports), batch_size):
        end = batch_start + batch_size
        batch = ports[batch_start:end]
        match = _probe_hook_ports_parallel(batch, timeout=timeout)
        if match is not None:
            return match
    return None


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
        return default_reverse_port()


def find_hook_server_port(
    base_port: int | None = None,
    *,
    max_attempts: int = 100,
    timeout: float | None = None,
) -> int | None:
    """Scan ports starting at *base_port* for a CYT server with ``hook: true``."""
    start = base_port if base_port is not None else resolve_hook_base_port()
    ports = [start + offset for offset in range(max_attempts)]
    return _find_hook_server_in_ports(ports, timeout=timeout)


def hook_port_is_live(port: int, *, timeout: float | None = None) -> bool:
    """Return True when *port* is bound and serving a hook server."""
    if not is_port_in_use(port):
        return False
    probe_timeout = STATUS_HEALTH_TIMEOUT_SECONDS if timeout is None else timeout
    return is_hook_server(fetch_cyt_health(port, timeout=probe_timeout))


def _collect_status_probe_ports(
    base_port: int,
    entries: list[dict[str, Any]],
    pidfile: dict[str, Any] | None,
) -> list[int]:
    ports: list[int] = []
    seen: set[int] = set()
    for entry in [*entries, *([pidfile] if pidfile else [])]:
        port = entry.get("port")
        if isinstance(port, int) and port not in seen:
            seen.add(port)
            ports.append(port)
    if base_port not in seen:
        ports.append(base_port)
    return ports


def collect_known_hook_ports(
    base_port: int,
    entries: list[dict[str, Any]],
) -> list[int]:
    """Return registry and configured ports to probe or stop without a full scan."""
    return _collect_status_probe_ports(base_port, entries, None)


def find_reusable_hook_port(
    base_port: int,
    entries: list[dict[str, Any]],
    *,
    full_scan: bool = False,
    max_attempts: int = 100,
) -> int | None:
    """Return a live hook-server port, preferring known registry/config ports."""
    known_ports = collect_known_hook_ports(base_port, entries)
    match = _find_hook_server_in_ports(known_ports, timeout=STATUS_HEALTH_TIMEOUT_SECONDS)
    if match is not None or not full_scan:
        return match
    ports = [base_port + offset for offset in range(max_attempts)]
    return _find_hook_server_in_ports(ports)


def _find_free_port_batch(ports: list[int]) -> int | None:
    if not ports:
        return None
    if len(ports) == 1:
        return None if is_port_in_use(ports[0]) else ports[0]

    free_ports: list[int] = []
    with ThreadPoolExecutor(max_workers=len(ports)) as executor:
        futures = {executor.submit(is_port_in_use, port): port for port in ports}
        for future in as_completed(futures):
            port = futures[future]
            try:
                if not future.result():
                    free_ports.append(port)
            except Exception:
                continue
    return min(free_ports) if free_ports else None


def find_spawn_port(
    base_port: int,
    *,
    max_attempts: int = 100,
    prefer_port: int | None = None,
) -> int:
    """Pick a free port for spawning, probing candidates in parallel batches."""
    if prefer_port is not None and not is_port_in_use(prefer_port):
        if not hook_port_is_live(prefer_port):
            return prefer_port

    for batch_start in range(0, max_attempts, HOOK_PORT_PROBE_BATCH_SIZE):
        batch_end = min(batch_start + HOOK_PORT_PROBE_BATCH_SIZE, max_attempts)
        batch = [base_port + offset for offset in range(batch_start, batch_end)]
        free_port = _find_free_port_batch(batch)
        if free_port is not None:
            return free_port
        if _probe_hook_ports_parallel(batch) is not None:
            continue
    return find_available_port(base_port, max_attempts=max_attempts)


def find_hook_port_for_status(
    base_port: int,
    entries: list[dict[str, Any]],
    pidfile: dict[str, Any] | None,
) -> int | None:
    """Probe only recorded and configured ports for ``hook daemon status``."""
    ports = _collect_status_probe_ports(base_port, entries, pidfile)
    if not ports:
        return None
    if len(ports) == 1:
        return (
            ports[0]
            if _is_hook_server_on_port(ports[0], timeout=STATUS_HEALTH_TIMEOUT_SECONDS)
            else None
        )

    live_ports: set[int] = set()
    with ThreadPoolExecutor(max_workers=len(ports)) as executor:
        futures = {
            executor.submit(
                _is_hook_server_on_port,
                port,
                timeout=STATUS_HEALTH_TIMEOUT_SECONDS,
            ): port
            for port in ports
        }
        for future in as_completed(futures):
            port = futures[future]
            try:
                if future.result():
                    live_ports.add(port)
            except Exception:
                continue
    for port in ports:
        if port in live_ports:
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

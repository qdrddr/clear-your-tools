"""Per-server FastMCP proxy mounts with fault isolation."""

from __future__ import annotations

import logging
import weakref
from typing import Any

from fastmcp import FastMCP
from fastmcp.server import create_proxy

logger = logging.getLogger(__name__)

_MOUNTED_BACKEND_SERVERS: weakref.WeakKeyDictionary[FastMCP[Any], set[str]] = (
    weakref.WeakKeyDictionary()
)


def mount_backend_servers(
    server: FastMCP,
    mcp_servers: dict[str, Any],
) -> list[str]:
    """Mount each configured backend; return names of degraded (failed) servers."""
    degraded: list[str] = []
    for name, spec in mcp_servers.items():
        server_key = str(name).strip()
        if not server_key or not isinstance(spec, dict):
            continue
        try:
            proxy = create_proxy({"mcpServers": {server_key: spec}}, name=f"cyt-mcp-{server_key}")
            server.mount(proxy, namespace=server_key)
        except Exception as exc:
            logger.warning("cyt-mcp: backend %s unavailable: %s", server_key, exc)
            degraded.append(server_key)
    return degraded


def mounted_backend_server_names(server: FastMCP[Any]) -> set[str]:
    """Return backend server keys already mounted on *server*."""
    return _MOUNTED_BACKEND_SERVERS.get(server, set())


def ensure_backend_servers_mounted(
    server: FastMCP,
    mcp_servers: dict[str, Any],
) -> list[str]:
    """Mount any not-yet-mounted backend proxies (lazy mount for stdio startup)."""
    mounted = mounted_backend_server_names(server)
    new_servers = {
        name: spec
        for name, spec in mcp_servers.items()
        if str(name).strip() and str(name).strip() not in mounted
    }
    if not new_servers:
        _MOUNTED_BACKEND_SERVERS[server] = mounted
        return []
    degraded = mount_backend_servers(server, new_servers)
    mounted.update(str(name).strip() for name in new_servers)
    _MOUNTED_BACKEND_SERVERS[server] = mounted
    return degraded

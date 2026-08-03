"""Per-server FastMCP proxy mounts with fault isolation."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.server import create_proxy

logger = logging.getLogger(__name__)


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

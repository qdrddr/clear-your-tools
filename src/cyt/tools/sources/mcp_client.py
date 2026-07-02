"""Load tool catalogs from MCP server configs via FastMCP."""

from __future__ import annotations

import importlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CLAUDE_MCP_FALLBACK = Path("~/.claude/claude.json")
_CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class _CacheKey:
    source_path: str
    mtime_ns: int
    tools_from: str


_cache: dict[_CacheKey, tuple[float, list[dict[str, Any]]]] = {}


def load_mcp_client_tools(
    config_path: Path,
    *,
    claude_fallback: bool = True,
) -> list[dict[str, Any]]:
    """List tools from MCP servers configured in *config_path*."""
    resolved = config_path.expanduser()
    mtime_ns = resolved.stat().st_mtime_ns if resolved.is_file() else 0
    key = _CacheKey(str(resolved), mtime_ns, "client")
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    servers = _load_mcp_servers(resolved, claude_fallback=claude_fallback)
    tools = _list_tools_from_servers(servers)
    _cache[key] = (now, tools)
    return tools


def _load_mcp_servers(path: Path, *, claude_fallback: bool) -> dict[str, Any]:
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        servers = data.get("mcpServers")
        if isinstance(servers, dict):
            return servers

    if claude_fallback:
        fallback = _CLAUDE_MCP_FALLBACK.expanduser()
        if fallback.is_file():
            data = json.loads(fallback.read_text(encoding="utf-8"))
            servers = data.get("mcpServers")
            if isinstance(servers, dict):
                return servers
    return {}


def _fastmcp_client_cls() -> type[Any]:
    try:
        fastmcp = importlib.import_module("fastmcp")
    except ImportError as exc:
        raise ImportError(
            "fastmcp is required for tools_from: client; "
            "install with: uv pip install 'clear-your-tools[mcp]'",
        ) from exc
    client_cls = getattr(fastmcp, "Client", None)
    if not isinstance(client_cls, type):
        raise ImportError("fastmcp.Client is unavailable")
    return client_cls


def _list_tools_from_servers(servers: dict[str, Any]) -> list[dict[str, Any]]:
    client_cls = _fastmcp_client_cls()
    tools: list[dict[str, Any]] = []
    for server_name, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            continue
        try:
            server_tools = _list_server_tools(client_cls, server_name, server_cfg)
        except Exception:
            logger.debug("failed to list tools for MCP server %s", server_name, exc_info=True)
            continue
        tools.extend(server_tools)
    return tools


def _list_server_tools(
    client_cls: type,
    server_name: str,
    server_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    transport = _server_transport(server_cfg)
    if transport is None:
        return []

    with client_cls(transport) as client:
        listed = client.list_tools()

    normalized: list[dict[str, Any]] = []
    for tool in listed:
        tool_name = getattr(tool, "name", None) or (
            tool.get("name") if isinstance(tool, dict) else None
        )
        if not tool_name:
            continue
        description = getattr(tool, "description", None)
        if description is None and isinstance(tool, dict):
            description = tool.get("description")
        input_schema = getattr(tool, "inputSchema", None)
        if input_schema is None:
            input_schema = getattr(tool, "input_schema", None)
        if input_schema is None and isinstance(tool, dict):
            input_schema = tool.get("inputSchema") or tool.get("input_schema")

        entry: dict[str, Any] = {
            "name": f"mcp__{server_name}__{tool_name}",
        }
        if description:
            entry["description"] = str(description)
        if isinstance(input_schema, dict):
            entry["input_schema"] = input_schema
        normalized.append(entry)
    return normalized


def _server_transport(server_cfg: dict[str, Any]) -> dict[str, Any] | str | None:
    if url := server_cfg.get("url"):
        return str(url)
    command = server_cfg.get("command")
    if not command:
        return None
    args = server_cfg.get("args") or []
    if not isinstance(args, list):
        args = []
    transport: dict[str, Any] = {
        "command": str(command),
        "args": [str(arg) for arg in args],
    }
    env = server_cfg.get("env")
    if isinstance(env, dict) and env:
        transport["env"] = {str(k): str(v) for k, v in env.items()}
    return transport

"""Load tool catalogs from MCP server configs via FastMCP."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

McpServerStatus = Literal["ok", "failed", "skipped"]


@dataclass(frozen=True)
class McpServerFetchResult:
    server_name: str
    status: McpServerStatus
    tools: tuple[dict[str, Any], ...]
    error: str | None = None


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
    tools, _results = fetch_mcp_client_tools(
        config_path,
        claude_fallback=claude_fallback,
        use_cache=True,
    )
    return tools


def fetch_mcp_client_tools(
    config_path: Path,
    *,
    claude_fallback: bool = True,
    use_cache: bool = False,
) -> tuple[list[dict[str, Any]], list[McpServerFetchResult]]:
    """List tools from MCP servers and return per-server fetch results."""
    resolved = config_path.expanduser()
    mtime_ns = resolved.stat().st_mtime_ns if resolved.is_file() else 0
    key = _CacheKey(str(resolved), mtime_ns, "client")
    now = time.monotonic()
    if use_cache:
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1], []

    servers = _load_mcp_servers(resolved, claude_fallback=claude_fallback)
    results = _fetch_tools_from_servers(servers)
    tools = [tool for result in results for tool in result.tools]
    if use_cache:
        _cache[key] = (now, tools)
    return tools, results


def _read_mcp_servers(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("invalid MCP client JSON in %s", path, exc_info=True)
        return {}
    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        return servers
    return {}


def _load_mcp_servers(path: Path, *, claude_fallback: bool) -> dict[str, Any]:
    servers = _read_mcp_servers(path)
    if servers:
        return servers

    if claude_fallback:
        return _read_mcp_servers(_CLAUDE_MCP_FALLBACK.expanduser())
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


def _fetch_tools_from_servers(servers: dict[str, Any]) -> list[McpServerFetchResult]:
    if not servers:
        return []

    client_cls = _fastmcp_client_cls()
    return asyncio.run(_fetch_tools_from_servers_async(client_cls, servers))


async def _fetch_tools_from_servers_async(
    client_cls: type[Any],
    servers: dict[str, Any],
) -> list[McpServerFetchResult]:
    results: list[McpServerFetchResult] = []
    for server_name, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            results.append(
                McpServerFetchResult(
                    server_name=str(server_name),
                    status="skipped",
                    tools=(),
                    error="server entry is not an object",
                ),
            )
            continue
        if not _server_has_transport(server_cfg):
            results.append(
                McpServerFetchResult(
                    server_name=str(server_name),
                    status="skipped",
                    tools=(),
                    error="missing url or command",
                ),
            )
            continue
        try:
            server_tools = await _list_server_tools_async(
                client_cls,
                str(server_name),
                server_cfg,
            )
        except Exception as exc:
            logger.debug(
                "failed to list tools for MCP server %s",
                server_name,
                exc_info=True,
            )
            results.append(
                McpServerFetchResult(
                    server_name=str(server_name),
                    status="failed",
                    tools=(),
                    error=str(exc),
                ),
            )
            continue
        results.append(
            McpServerFetchResult(
                server_name=str(server_name),
                status="ok",
                tools=tuple(server_tools),
            ),
        )
    return results


def _list_tools_from_servers(servers: dict[str, Any]) -> list[dict[str, Any]]:
    results = _fetch_tools_from_servers(servers)
    tools: list[dict[str, Any]] = []
    for result in results:
        if result.status == "ok":
            tools.extend(result.tools)
    return tools


def _fastmcp_client_config(server_name: str, server_cfg: dict[str, Any]) -> dict[str, Any]:
    """Wrap one server entry in the MCP config shape FastMCP expects."""
    return {"mcpServers": {server_name: server_cfg}}


def _server_has_transport(server_cfg: dict[str, Any]) -> bool:
    if server_cfg.get("url"):
        return True
    return bool(server_cfg.get("command"))


async def _list_server_tools_async(
    client_cls: type[Any],
    server_name: str,
    server_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    client = client_cls(_fastmcp_client_config(server_name, server_cfg))
    async with client as connected:
        listed = await connected.list_tools()

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

"""List MCP servers and tools for permissions CLI."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cyt.permissions.match import (
    explicit_denied_servers,
    explicit_denied_tools_for_server,
    is_mcp_server_denied,
    is_mcp_tool_denied,
    split_catalog_tool_name,
)
from cyt.permissions.merge import effective_permissions
from cyt.permissions.paths import (
    InventoryScope,
    PermissionScope,
    mcp_server_defs_path,
    resolve_inventory_agent,
)

McpServerSource = Literal["user", "workspace"]


@dataclass(frozen=True)
class McpServerInventoryItem:
    name: str
    enabled: bool
    source: McpServerSource | None = None


@dataclass(frozen=True)
class McpToolInventoryItem:
    server: str
    tool: str
    catalog_name: str
    enabled: bool


def _inventory_layers(scope: InventoryScope) -> tuple[PermissionScope, ...]:
    if scope == "effective":
        return ("global", "workspace")
    return (scope,)


def load_mcp_server_names(
    *,
    agent: str,
    scope: InventoryScope = "effective",
    workspace_root: Path | None = None,
) -> list[str]:
    """Return backend MCP server names from cyt MCP JSON for *scope*."""
    return sorted(
        load_mcp_server_sources(
            agent=agent,
            scope=scope,
            workspace_root=workspace_root,
        ),
    )


def load_mcp_server_sources(
    *,
    agent: str,
    scope: InventoryScope = "effective",
    workspace_root: Path | None = None,
) -> dict[str, McpServerSource]:
    """Return backend MCP server names and their config layer (no MCP runtime)."""
    resolved = resolve_inventory_agent(agent)
    sources: dict[str, McpServerSource] = {}
    for layer in _inventory_layers(scope):
        if layer == "workspace":
            from cyt.hook.install_scope import CytInstallScope

            scope_obj = CytInstallScope.from_cwd()
            if workspace_root is None and not scope_obj.has_workspace:
                continue
        path = mcp_server_defs_path(
            agent=resolved,
            scope=layer,
            workspace_root=workspace_root,
        )
        source: McpServerSource = "user" if layer == "global" else "workspace"
        for name in _read_mcp_server_names_from_path(path):
            if source == "workspace" or name not in sources:
                sources[name] = source
    return sources


def _read_mcp_server_names_from_path(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    return [str(name) for name in servers if str(name).strip()]


def list_mcp_servers(
    *,
    agent: str,
    scope: InventoryScope = "effective",
    workspace_root: Path | None = None,
    policy_agent: str | None = None,
) -> tuple[list[McpServerInventoryItem], list[McpServerInventoryItem]]:
    resolved = resolve_inventory_agent(agent)
    policy = (policy_agent or agent or "all").strip().lower() or "all"
    sources = load_mcp_server_sources(
        agent=resolved,
        scope=scope,
        workspace_root=workspace_root,
    )
    names = set(sources)
    effective = effective_permissions(agent=policy, workspace_root=workspace_root)
    deny_entries = effective.mcp.deny
    for server_name in explicit_denied_servers(deny_entries):
        names.add(server_name)

    enabled: list[McpServerInventoryItem] = []
    disabled: list[McpServerInventoryItem] = []
    for name in sorted(names):
        item = McpServerInventoryItem(
            name=name,
            enabled=not is_mcp_server_denied(name, deny_entries),
            source=sources.get(name),
        )
        if item.enabled:
            enabled.append(item)
        else:
            disabled.append(item)
    return enabled, disabled


async def _fetch_catalog_tools(
    *,
    agent: str,
    scope: InventoryScope,
    workspace_root: Path | None = None,
    for_permissions_inventory: bool = False,
) -> list[dict[str, Any]]:
    if scope == "effective":
        merged: dict[str, dict[str, Any]] = {}
        for layer in _inventory_layers(scope):
            for tool in await _fetch_catalog_tools(
                agent=agent,
                scope=layer,
                workspace_root=workspace_root,
                for_permissions_inventory=for_permissions_inventory,
            ):
                name = str(tool.get("name") or "").strip()
                if name:
                    merged[name] = tool
        return list(merged.values())

    from dataclasses import replace

    from cyt_mcp.aggregator import build_aggregator
    from cyt_mcp.config import load_aggregator_config, load_mcp_servers
    from cyt_mcp.runtime_cache import RuntimeToolCache
    from cyt_mcp.transport import refresh_runtime_cache

    resolved = resolve_inventory_agent(agent)
    if scope == "workspace":
        from cyt_mcp.workspace_catalog import workspace_aggregator_path

        root = workspace_root or Path.cwd()
        agg = workspace_aggregator_path(root, resolved)
        if agg is None or not agg.is_file():
            return []
        config = load_aggregator_config(agent=resolved, aggregator_path=agg, workspace_folder=root)
    else:
        config = load_aggregator_config(agent=resolved, workspace_folder=workspace_root)

    if for_permissions_inventory:
        config = replace(
            config,
            mcp_deny=(),
            mcp_servers=load_mcp_servers(
                config.agent_mcp_path,
                workspace_folder=config.workspace_root or workspace_root,
                deny_entries=(),
            ),
        )

    cache = RuntimeToolCache()
    server = build_aggregator(config, cache)
    await refresh_runtime_cache(server, cache, config)
    return cache.snapshot()


def fetch_catalog_tools_sync(
    *,
    agent: str,
    scope: InventoryScope = "effective",
    workspace_root: Path | None = None,
    for_permissions_inventory: bool = False,
) -> list[dict[str, Any]]:
    try:
        return asyncio.run(
            _fetch_catalog_tools(
                agent=agent,
                scope=scope,
                workspace_root=workspace_root,
                for_permissions_inventory=for_permissions_inventory,
            ),
        )
    except Exception:
        return []


def list_mcp_tools_for_server(
    server: str,
    *,
    agent: str,
    scope: InventoryScope = "effective",
    workspace_root: Path | None = None,
    policy_agent: str | None = None,
    catalog_tools: list[dict[str, Any]] | None = None,
) -> tuple[list[McpToolInventoryItem], list[McpToolInventoryItem]]:
    resolved = resolve_inventory_agent(agent)
    policy = (policy_agent or agent or "all").strip().lower() or "all"
    server_name = server.strip()
    tools_raw = catalog_tools
    if tools_raw is None:
        tools_raw = fetch_catalog_tools_sync(
            agent=resolved,
            scope=scope,
            workspace_root=workspace_root,
            for_permissions_inventory=True,
        )
    effective = effective_permissions(agent=policy, workspace_root=workspace_root)
    deny_entries = effective.mcp.deny
    items_by_tool: dict[str, McpToolInventoryItem] = {}
    for tool in tools_raw:
        catalog_name = str(tool.get("name") or "").strip()
        if not catalog_name:
            continue
        parts = split_catalog_tool_name(catalog_name)
        if parts is None:
            continue
        tool_server, tool_name = parts
        if tool_server != server_name:
            continue
        items_by_tool[tool_name] = McpToolInventoryItem(
            server=tool_server,
            tool=tool_name,
            catalog_name=catalog_name,
            enabled=not is_mcp_tool_denied(tool_server, tool_name, deny_entries),
        )

    for tool_name in explicit_denied_tools_for_server(server_name, deny_entries):
        if tool_name in items_by_tool:
            continue
        items_by_tool[tool_name] = McpToolInventoryItem(
            server=server_name,
            tool=tool_name,
            catalog_name=f"{server_name}_{tool_name}",
            enabled=False,
        )

    enabled: list[McpToolInventoryItem] = []
    disabled: list[McpToolInventoryItem] = []
    for item in items_by_tool.values():
        if item.enabled:
            enabled.append(item)
        else:
            disabled.append(item)
    enabled.sort(key=lambda row: row.tool)
    disabled.sort(key=lambda row: row.tool)
    return enabled, disabled

"""Build hook-daemon catalog and search index from backend FastMCP tools."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.tools.base import Tool
from mcp.types import Tool as McpWireTool

from cyt_mcp.config import AggregatorConfig
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME, refresh_search_tool_schema
from cyt_mcp.tool_identity import enrich_tool_identity, tool_name_allowed_for_servers

JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def json_safe_value(value: object) -> JsonValue:
    """Recursively convert MCP/Pydantic objects to JSON-serializable values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json", exclude_none=True)
        except TypeError:
            dumped = model_dump(exclude_none=True)
        return json_safe_value(dumped)
    return str(value)


def mcp_tool_to_catalog_dict(mcp_tool: McpWireTool) -> dict[str, Any]:
    schema_dict = dict(mcp_tool.inputSchema)
    entry: dict[str, Any] = {
        "name": str(mcp_tool.name),
        "inputSchema": schema_dict,
    }
    if mcp_tool.description:
        entry["description"] = str(mcp_tool.description)
    title = getattr(mcp_tool, "title", None)
    if title:
        entry["title"] = str(title)
    return entry


def mcp_tool_to_search_index_entry(mcp_tool: McpWireTool) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": str(mcp_tool.name),
        "inputSchema": dict(mcp_tool.inputSchema),
    }
    if mcp_tool.description:
        entry["description"] = str(mcp_tool.description)
    title = getattr(mcp_tool, "title", None)
    if title:
        entry["title"] = str(title)
    output_schema = getattr(mcp_tool, "outputSchema", None)
    if output_schema is not None:
        entry["outputSchema"] = output_schema
    if mcp_tool.annotations is not None:
        entry["annotations"] = mcp_tool.annotations
    if mcp_tool.execution is not None:
        entry["execution"] = mcp_tool.execution
    if mcp_tool.meta is not None:
        entry["meta"] = mcp_tool.meta
    return cast(dict[str, Any], json_safe_value(entry))


def build_catalog_from_tools(
    tools: Sequence[Tool],
    *,
    deny_entries: tuple[str, ...] | list[str] | None = None,
    mcp_server_keys: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    catalog_entries: list[dict[str, Any]] = []
    search_index: dict[str, dict[str, Any]] = {}
    deny_filter = deny_entries or ()
    server_keys = list(mcp_server_keys or [])
    for tool in tools:
        mcp_tool = tool.to_mcp_tool()
        name = str(mcp_tool.name)
        if name == MCP_WIRE_SEARCH_TOOL_NAME:
            continue
        if server_keys and not tool_name_allowed_for_servers(name, server_keys):
            continue
        if deny_filter:
            from cyt.permissions.match import is_catalog_tool_denied

            if is_catalog_tool_denied(name, deny_filter):
                continue
        catalog_entries.append(mcp_tool_to_catalog_dict(mcp_tool))
        search_index[name] = mcp_tool_to_search_index_entry(mcp_tool)
    return catalog_entries, search_index


_refresh_locks: dict[int, asyncio.Lock] = {}


def _refresh_lock_for(cache: RuntimeToolCache) -> asyncio.Lock:
    key = id(cache)
    lock = _refresh_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[key] = lock
    return lock


async def wait_for_catalog_cache_ready(
    cache: RuntimeToolCache,
    *,
    max_wait_seconds: float | None = 3.0,
) -> int:
    """Wait briefly for an in-flight refresh on *cache*, then return entry count."""
    lock = _refresh_lock_for(cache)
    if max_wait_seconds is None or max_wait_seconds <= 0:
        async with lock:
            return len(cache.snapshot())
    try:
        await asyncio.wait_for(lock.acquire(), timeout=max_wait_seconds)
    except TimeoutError:
        return len(cache.snapshot())
    try:
        return len(cache.snapshot())
    finally:
        lock.release()


def persist_runtime_cache_to_disk(cache: RuntimeToolCache, config: AggregatorConfig) -> bool:
    """Write the runtime catalog to disk so the next process can hydrate quickly."""
    from cyt.cyt_mcp.catalog_disk import raw_catalog_content_hash, write_disk_catalog

    slug = disk_catalog_slug_for_config(config)
    if not slug:
        return False
    tools = cache.snapshot()
    if not tools:
        return False
    existing_disk = _read_disk_catalog_tools(slug)
    if existing_disk and len(tools) < len(existing_disk):
        return False
    content_hash = raw_catalog_content_hash(tools)
    action = write_disk_catalog(
        slug,
        agent=config.agent,
        tools=tools,
        content_hash=content_hash,
    )
    return action != "disk_write_skipped"


def _workspace_scope_fingerprint(config: AggregatorConfig) -> str | None:
    from pathlib import Path

    from cyt.cyt_mcp.catalog_disk import scope_config_fingerprint
    from cyt_mcp.config import DEFAULT_MCP_CONFIG_PATH

    if config.workspace_root is None:
        return None
    agg_path = config.aggregator_path or DEFAULT_MCP_CONFIG_PATH.expanduser()
    defs_path = config.agent_mcp_path
    try:
        workspace_key = str(config.workspace_root.expanduser().resolve())
    except OSError:
        workspace_key = str(config.workspace_root)
    return scope_config_fingerprint(
        Path(agg_path),
        Path(defs_path),
        version=workspace_key,
    )


def merged_hook_disk_catalog_slug(config: AggregatorConfig) -> str | None:
    """Merged usr+ws slug used by hook injection disk cache (legacy hydrate fallback)."""
    from pathlib import Path

    from cyt.cyt_mcp.catalog_disk import merged_hook_catalog_slug, scope_config_fingerprint
    from cyt_mcp.config import DEFAULT_MCP_CONFIG_PATH

    ws_fp = _workspace_scope_fingerprint(config)
    if ws_fp is None:
        return None
    agent = config.agent.strip() or "cursor"
    user_agg = DEFAULT_MCP_CONFIG_PATH.expanduser()
    user_defs = Path(f"~/.config/cyt/mcp/{agent}.json").expanduser()
    global_fp = scope_config_fingerprint(user_agg, user_defs)
    return merged_hook_catalog_slug(global_fp, ws_fp)


def disk_catalog_slug_for_config(config: AggregatorConfig) -> str | None:
    """Runtime disk slug for this cyt-mcp frontend instance (usr or ws scope only)."""
    from pathlib import Path

    from cyt.cyt_mcp.catalog_disk import scope_config_fingerprint
    from cyt_mcp.config import DEFAULT_MCP_CONFIG_PATH

    agent = config.agent.strip() or "cursor"
    user_agg = DEFAULT_MCP_CONFIG_PATH.expanduser()
    user_defs = Path(f"~/.config/cyt/mcp/{agent}.json").expanduser()
    if config.catalog_scope != "workspace" or config.workspace_root is None:
        return scope_config_fingerprint(user_agg, user_defs)
    ws_fp = _workspace_scope_fingerprint(config)
    return ws_fp or scope_config_fingerprint(user_agg, user_defs)


def _search_index_from_catalog_entry(entry: dict[str, Any]) -> dict[str, Any]:
    schema = entry.get("inputSchema")
    if not isinstance(schema, dict):
        schema = entry.get("input_schema")
    index: dict[str, Any] = {
        "name": str(entry.get("name") or ""),
        "inputSchema": dict(schema) if isinstance(schema, dict) else {},
    }
    for key in (
        "description",
        "title",
        "outputSchema",
        "annotations",
        "execution",
        "meta",
        "server_key",
        "tool_name",
    ):
        if key in entry and entry[key] is not None:
            index[key] = entry[key]
    return index


def _catalog_entries_from_dicts(
    tools: list[dict[str, Any]],
    config: AggregatorConfig,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    from cyt.permissions.match import is_catalog_tool_denied

    deny_entries = config.mcp_deny
    server_keys = sorted(config.mcp_servers.keys(), key=len, reverse=True)
    catalog_entries: list[dict[str, Any]] = []
    search_index: dict[str, dict[str, Any]] = {}
    for raw in tools:
        name = str(raw.get("name") or "").strip()
        if not name or name == MCP_WIRE_SEARCH_TOOL_NAME:
            continue
        if server_keys and not tool_name_allowed_for_servers(name, server_keys):
            continue
        if deny_entries and is_catalog_tool_denied(name, deny_entries):
            continue
        entry = dict(raw)
        schema = entry.get("inputSchema")
        if not isinstance(schema, dict):
            schema = entry.get("input_schema")
        if isinstance(schema, dict):
            entry["inputSchema"] = dict(schema)
        entry = enrich_tool_identity(entry, server_keys)
        catalog_entries.append(entry)
        search_index[name] = _search_index_from_catalog_entry(entry)
    return catalog_entries, search_index


def _catalog_tools_from_registry(config: AggregatorConfig) -> list[dict[str, Any]]:
    if config.workspace_root is None:
        return []
    from cyt.hook.catalog_registry import catalog_for_layer, load_catalog_registry_from_disk
    from cyt_mcp.config import catalog_layer_for_scope

    load_catalog_registry_from_disk(mark_stale=True)
    layer = catalog_layer_for_scope(config.catalog_scope)
    return catalog_for_layer(
        config.agent,
        config.workspace_root,
        layer,
        allow_stale=True,
    )


def _read_disk_catalog_tools(slug: str) -> list[dict[str, Any]]:
    from cyt.cyt_mcp.catalog_disk import read_disk_catalog

    envelope = read_disk_catalog(slug)
    if envelope is None:
        return []
    tools = envelope.get("tools")
    if not isinstance(tools, list):
        return []
    return [dict(item) for item in tools if isinstance(item, dict)]


def _catalog_tools_from_disk(config: AggregatorConfig) -> list[dict[str, Any]]:
    slug_candidates: list[str] = []
    primary = disk_catalog_slug_for_config(config)
    if primary:
        slug_candidates.append(primary)
    if config.catalog_scope == "workspace":
        merged = merged_hook_disk_catalog_slug(config)
        if merged and merged not in slug_candidates:
            slug_candidates.append(merged)
    for slug in slug_candidates:
        tools = _read_disk_catalog_tools(slug)
        if tools:
            return tools
    return []


def hydrate_runtime_cache(cache: RuntimeToolCache, config: AggregatorConfig) -> bool:
    """Warm runtime cache from registry/disk so list_tools works before live backend fetch."""
    # Disk is written by this frontend instance; registry may hold stale/partial layers.
    disk_tools = _catalog_tools_from_disk(config)
    registry_tools = _catalog_tools_from_registry(config)
    if disk_tools and registry_tools:
        if len(disk_tools) >= len(registry_tools):
            tools = disk_tools
        else:
            tools = registry_tools
    elif disk_tools:
        tools = disk_tools
    elif registry_tools:
        tools = registry_tools
    else:
        tools = []
    if not tools:
        return False
    catalog_entries, search_index = _catalog_entries_from_dicts(tools, config)
    if not catalog_entries:
        return False
    cache.replace(catalog_entries, search_index=search_index)
    refresh_search_tool_schema(cache)
    return True


def reapply_deny_overlays(cache: RuntimeToolCache, config: AggregatorConfig) -> bool:
    """Re-filter cached catalog entries by the current deny list."""
    from cyt.permissions.match import is_catalog_tool_denied

    deny_entries = config.mcp_deny
    entries = cache.snapshot()
    search_index = cache.search_index_snapshot()
    if not entries:
        return False
    filtered_entries = [
        entry
        for entry in entries
        if not is_catalog_tool_denied(str(entry.get("name") or ""), deny_entries)
    ]
    allowed_names = {str(entry.get("name") or "") for entry in filtered_entries}
    filtered_index = {name: value for name, value in search_index.items() if name in allowed_names}
    if len(filtered_entries) == len(entries):
        return False
    if not filtered_entries and entries:
        return False
    cache.replace(filtered_entries, search_index=filtered_index)
    refresh_search_tool_schema(cache)
    return True


async def refresh_catalog_cache(
    server: FastMCP,
    cache: RuntimeToolCache,
    config: AggregatorConfig | None = None,
    *,
    skip_push: bool = False,
    force: bool = False,
) -> None:
    """Populate hook-daemon catalog + search index from raw backend tools."""
    async with _refresh_lock_for(cache):
        _before = len(cache.snapshot())
        if _before > 0 and not force:
            return
        if config is not None and config.mcp_servers:
            from cyt_mcp.backends import ensure_backend_servers_mounted

            await asyncio.to_thread(ensure_backend_servers_mounted, server, config.mcp_servers)

        backend_server = cast(Any, server)

        async def _list_backend_tools() -> list[Tool]:
            listed = backend_server._list_tools()
            if asyncio.iscoroutine(listed) or asyncio.isfuture(listed):
                return cast(list[Tool], await listed)
            return cast(list[Tool], await asyncio.to_thread(listed))

        backend_tools = await _list_backend_tools()
        deny_entries = config.mcp_deny if config is not None else ()
        server_keys = (
            sorted(config.mcp_servers.keys(), key=len, reverse=True) if config is not None else []
        )
        catalog_entries, search_index = build_catalog_from_tools(
            backend_tools,
            deny_entries=deny_entries,
            mcp_server_keys=server_keys,
        )
        if config is not None:
            server_keys = sorted(config.mcp_servers.keys(), key=len, reverse=True)
            catalog_entries = [
                enrich_tool_identity(entry, server_keys) for entry in catalog_entries
            ]
        if not catalog_entries and _before > 0:
            return
        cache.replace(catalog_entries, search_index=search_index)
        refresh_search_tool_schema(cache)
        if config is not None:
            persist_runtime_cache_to_disk(cache, config)
        if config is not None:
            from cyt_mcp.hook_daemon_push import schedule_catalog_push

            schedule_catalog_push(cache, config, skip_push=skip_push)

"""Export backend and frontend cyt-mcp tool catalogs with token accounting."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from fastmcp import FastMCP

from cyt.indexer.tokens import count_json_tokens
from cyt_mcp.catalog import catalog_payload
from cyt_mcp.catalog_build import json_safe_value, mcp_tool_to_catalog_dict
from cyt_mcp.config import AggregatorConfig
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.tool_identity import enrich_tool_identity
from cyt_mcp.search import (
    MCP_WIRE_SEARCH_TOOL_NAME,
    SEARCH_TOOL_NAME,
    build_search_input_schema,
    search_tool_description,
)
from cyt_mcp.stub_catalog import RetainSpec, retain_includes_required_names, retain_includes_tool_field
from cyt_mcp.stubs import _minimal_required_schema


def _input_schema_from_hook_tool(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        schema = tool.get("input_schema")
    return dict(schema) if isinstance(schema, dict) else {}


def stub_dict_from_hook_tool(tool: dict[str, Any], *, retain: RetainSpec) -> dict[str, Any]:
    """Project one hook-catalog tool to a frontend stub dict."""
    name = str(tool.get("name") or "").strip()
    entry: dict[str, Any] = {"name": name}
    if retain_includes_tool_field(retain, "description"):
        description = tool.get("description")
        if description is not None:
            entry["description"] = str(description)
    full_schema = _input_schema_from_hook_tool(tool)
    if retain_includes_required_names(retain):
        entry["inputSchema"] = _minimal_required_schema(full_schema)
    else:
        entry["inputSchema"] = {"type": "object", "properties": {}}
    return entry


def group_backend_catalog_payload(
    payload: dict[str, Any],
    *,
    server_origins: dict[str, str] | None = None,
    server_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Group a flat backend catalog export by MCP server key."""
    tools_raw = payload.get("tools")
    tools = tools_raw if isinstance(tools_raw, list) else []
    origins = server_origins or {}
    keys = list(server_keys or origins.keys())
    grouped: dict[str, list[dict[str, Any]]] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        enriched = enrich_tool_identity(dict(tool), keys) if keys else dict(tool)
        server_key = str(enriched.get("server_key") or "").strip() or "unknown"
        grouped.setdefault(server_key, []).append(json_safe_value(enriched))

    servers: dict[str, Any] = {}
    for server_key in sorted(grouped.keys()):
        block: dict[str, Any] = {
            "tools": sorted(
                grouped[server_key],
                key=lambda item: str(item.get("name") or ""),
            ),
        }
        origin = origins.get(server_key)
        if origin in {"user", "workspace"}:
            block["origin"] = origin
        servers[server_key] = block

    return {
        "agent": payload.get("agent"),
        "servers": servers,
        "degraded_servers": payload.get("degraded_servers") or [],
    }


def catalog_token_stats(tools: Sequence[dict[str, Any]] | Sequence[Any]) -> dict[str, int]:
    """Return tool count and compact-JSON token count for *tools*."""
    tool_list = [json_safe_value(tool) for tool in tools]
    return {
        "tool_count": len(tool_list),
        "tokens_compact_json": count_json_tokens(tool_list),
    }


def backend_hook_catalog(
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> dict[str, Any]:
    """Hook-daemon catalog snapshot grouped by MCP server (input_schema + identity)."""
    server_keys = list(config.mcp_servers.keys())
    flat = catalog_payload(
        cache,
        agent=config.agent,
        server_origins=config.server_origins,
        server_keys=server_keys,
    )
    return group_backend_catalog_payload(
        flat,
        server_origins=config.server_origins,
        server_keys=server_keys,
    )


def backend_full_catalog(
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> dict[str, Any]:
    """Full backend definitions grouped by MCP server (outputSchema, meta, etc.)."""
    server_keys = list(config.mcp_servers.keys())
    tools = sorted(
        cache.search_index_snapshot().values(),
        key=lambda item: str(item.get("name") or ""),
    )
    enriched_tools = [
        json_safe_value(enrich_tool_identity(dict(tool), server_keys))
        for tool in tools
    ]
    flat = {
        "agent": config.agent,
        "tools": enriched_tools,
        "degraded_servers": cache.degraded(),
    }
    return group_backend_catalog_payload(
        flat,
        server_origins=config.server_origins,
        server_keys=server_keys,
    )


def frontend_payload_from_hook_tools(
    hook_tools: list[dict[str, Any]],
    *,
    config: AggregatorConfig,
) -> dict[str, Any]:
    """Build frontend tools/list stubs from hook-catalog tool dicts."""
    stubs: list[dict[str, Any]] = []
    allowed_names: list[str] = []
    deny_entries = config.mcp_deny
    retain = config.stub_retain
    for tool in hook_tools:
        name = str(tool.get("name") or "").strip()
        if not name or name in {SEARCH_TOOL_NAME, MCP_WIRE_SEARCH_TOOL_NAME}:
            continue
        if deny_entries:
            from cyt.permissions.match import is_catalog_tool_denied

            if is_catalog_tool_denied(name, deny_entries):
                continue
        allowed_names.append(name)
        stubs.append(stub_dict_from_hook_tool(tool, retain=retain))
    stubs.append(
        {
            "name": MCP_WIRE_SEARCH_TOOL_NAME,
            "description": search_tool_description(agent=config.agent),
            "inputSchema": build_search_input_schema(allowed_names),
        },
    )
    return {"agent": config.agent, "tools": stubs}


async def frontend_stubs(
    server: FastMCP,
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> dict[str, Any]:
    """Export frontend tools/list stubs exactly as MCP clients receive them."""
    _ = cache
    backend_server = cast(Any, server)
    stub_tools = await backend_server.list_tools()
    tools = [
        json_safe_value(mcp_tool_to_catalog_dict(stub.to_mcp_tool()))
        for stub in stub_tools
    ]
    return {"agent": config.agent, "tools": tools}


def tool_dicts_from_frontend_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("tools")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def tool_dicts_from_backend_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    servers = payload.get("servers")
    if isinstance(servers, dict):
        tools: list[dict[str, Any]] = []
        for block in servers.values():
            if not isinstance(block, dict):
                continue
            raw = block.get("tools")
            if isinstance(raw, list):
                tools.extend(item for item in raw if isinstance(item, dict))
        return tools
    return tool_dicts_from_frontend_payload(payload)

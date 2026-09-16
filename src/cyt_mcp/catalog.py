"""Export full tool catalog for hook daemon (live in-memory only)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME, SEARCH_TOOL_NAME
from cyt_mcp.tool_identity import enrich_tool_identity


def _canonical_tool_entry(tool: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": str(tool.get("name") or ""),
    }
    if "description" in tool and tool["description"] is not None:
        entry["description"] = str(tool["description"])
    schema = tool.get("input_schema") or tool.get("inputSchema")
    if isinstance(schema, dict):
        entry["input_schema"] = schema
    else:
        entry["input_schema"] = {}
    return entry


def catalog_tools_content_hash(tools: list[dict[str, Any]]) -> str:
    canonical = [_canonical_tool_entry(tool) for tool in tools]
    canonical.sort(key=lambda item: item["name"])
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def catalog_payload(
    cache: RuntimeToolCache,
    *,
    agent: str,
    server_origins: Mapping[str, str] | None = None,
    server_keys: list[str] | None = None,
) -> dict[str, Any]:
    origins = server_origins or {}
    known_server_keys = list(server_keys or origins.keys())
    tools = cache.snapshot()
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if not name or name in {SEARCH_TOOL_NAME, MCP_WIRE_SEARCH_TOOL_NAME}:
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            schema = {}
        entry: dict[str, Any] = {
            "name": name,
            "input_schema": schema,
            "cyt_catalog_source": "cyt_mcp",
        }
        if tool.get("description") is not None:
            entry["description"] = str(tool["description"])
        if tool.get("server_key") is not None:
            entry["server_key"] = str(tool["server_key"])
        if tool.get("tool_name") is not None:
            entry["tool_name"] = str(tool["tool_name"])
        entry = enrich_tool_identity(entry, known_server_keys)
        server_key = str(entry.get("server_key") or "").strip()
        if server_key:
            origin = origins.get(server_key)
            if origin in {"user", "workspace"}:
                entry["cyt_catalog_scope"] = origin
        normalized.append(entry)
    return {
        "agent": agent,
        "tools": normalized,
        "degraded_servers": cache.degraded(),
    }


def _tools_indexed_by_name(tools_raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(tools_raw, list):
        return {}
    by_name: dict[str, dict[str, Any]] = {}
    for item in tools_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            by_name[name] = item
    return by_name


def merge_catalog_payloads(
    base: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, Any]:
    """Merge tool lists; workspace *overlay* overrides *base* on name conflict."""
    by_name = _tools_indexed_by_name(base.get("tools"))
    by_name.update(_tools_indexed_by_name(overlay.get("tools")))

    degraded: set[str] = set()
    for payload in (base, overlay):
        raw = payload.get("degraded_servers")
        if isinstance(raw, list):
            degraded.update(str(item).strip() for item in raw if str(item).strip())

    agent = str(overlay.get("agent") or base.get("agent") or "cursor")
    return {
        "agent": agent,
        "tools": list(by_name.values()),
        "degraded_servers": sorted(degraded),
    }


def catalog_json(
    cache: RuntimeToolCache,
    *,
    agent: str,
    server_origins: dict[str, str] | None = None,
) -> str:
    return json.dumps(
        catalog_payload(cache, agent=agent, server_origins=server_origins),
        ensure_ascii=False,
        indent=2,
    )

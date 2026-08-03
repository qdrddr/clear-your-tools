"""Export full tool catalog for hook daemon (live in-memory only)."""

from __future__ import annotations

import json
from typing import Any

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import SEARCH_TOOL_NAME


def catalog_payload(cache: RuntimeToolCache, *, agent: str) -> dict[str, Any]:
    tools = cache.snapshot()
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if not name or name == SEARCH_TOOL_NAME:
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
        server_key, _, tool_name = name.partition("_")
        if server_key and tool_name:
            entry["server_key"] = server_key
            entry["tool_name"] = tool_name
        normalized.append(entry)
    return {
        "agent": agent,
        "tools": normalized,
        "degraded_servers": cache.degraded(),
    }


def catalog_json(cache: RuntimeToolCache, *, agent: str) -> str:
    return json.dumps(catalog_payload(cache, agent=agent), ensure_ascii=False, indent=2)

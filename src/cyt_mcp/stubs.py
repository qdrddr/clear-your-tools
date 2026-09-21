"""Stub projection for frontend tools/list."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastmcp.server.transforms import Transform
from fastmcp.tools.base import Tool

from cyt_mcp.config import AggregatorConfig
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME
from cyt_mcp.session_context import get_current_session_runtime, is_session_scoping_active
from cyt_mcp.tool_identity import tool_name_allowed_for_servers
from cyt_mcp.stub_catalog import (
    RetainSpec,
    retain_includes_required_names,
    retain_includes_tool_field,
)

_MINIMAL_OBJECT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def _tool_input_schema(tool: Tool) -> dict[str, Any]:
    mcp_tool = tool.to_mcp_tool()
    params: Any = mcp_tool.inputSchema
    if isinstance(params, dict):
        return dict(params)
    return dict(_MINIMAL_OBJECT_SCHEMA)


def _required_property_names(schema: dict[str, Any]) -> list[str]:
    required = schema.get("required")
    if isinstance(required, list):
        return [str(name) for name in required if str(name).strip()]
    return []


def _minimal_required_schema(full_schema: dict[str, Any]) -> dict[str, Any]:
    properties = full_schema.get("properties")
    if not isinstance(properties, dict):
        return dict(_MINIMAL_OBJECT_SCHEMA)
    required_names = _required_property_names(full_schema)
    if not required_names:
        return dict(_MINIMAL_OBJECT_SCHEMA)
    minimal_props: dict[str, Any] = {}
    for name in required_names:
        prop = properties.get(name)
        if isinstance(prop, dict):
            minimal_props[name] = {
                key: prop[key] for key in ("type", "enum", "items", "anyOf", "oneOf") if key in prop
            } or {"type": prop.get("type", "string")}
        else:
            minimal_props[name] = {"type": "string"}
    return {
        "type": "object",
        "properties": minimal_props,
        "required": required_names,
    }


def _stub_parameters(tool: Tool, retain: RetainSpec) -> dict[str, Any]:
    if retain_includes_required_names(retain):
        return _minimal_required_schema(_tool_input_schema(tool))
    return dict(_MINIMAL_OBJECT_SCHEMA)


def _stub_from_tool(tool: Tool, *, retain: RetainSpec) -> Tool:
    include_description = retain_includes_tool_field(retain, "description")
    mcp_tool = tool.to_mcp_tool()
    description = (mcp_tool.description or "") if include_description else ""
    stub = Tool.from_tool(tool, description=description)
    return stub.model_copy(
        update={
            "description": description,
            "parameters": _stub_parameters(tool, retain),
        },
    )


class StubListTransform(Transform):
    """Expose minimal backend stubs and full get-tool-definitions to MCP clients."""

    def __init__(
        self,
        cache: RuntimeToolCache,
        *,
        retain: RetainSpec | None = None,
        include_description: bool | None = None,
        deny_entries: tuple[str, ...] | list[str] | None = None,
        config_holder: ConfigHolder | None = None,
    ) -> None:
        self._cache = cache
        if retain is None:
            retain = {
                "tool": ["name", "description"] if include_description else ["name"],
                "required_properties": [],
                "optional_properties": [],
            }
        self._retain = retain
        self._deny_entries = deny_entries or ()
        self._config_holder = config_holder

    def _active_runtime(self) -> tuple[RuntimeToolCache, ConfigHolder | None]:
        runtime = get_current_session_runtime()
        if runtime is not None:
            return runtime.cache, runtime.config_holder
        return self._cache, self._config_holder

    def _current_deny_entries(self) -> tuple[str, ...] | list[str]:
        _cache, config_holder = self._active_runtime()
        if config_holder is not None:
            return config_holder.mcp_deny
        return self._deny_entries

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        active_cache, config_holder = self._active_runtime()
        server_keys: list[str] = []
        if is_session_scoping_active() and config_holder is not None:
            server_keys = sorted(config_holder.config.mcp_servers.keys(), key=len, reverse=True)
        stubs: list[Tool] = []
        deny_entries = self._current_deny_entries()
        input_count = len(tools)
        for tool in tools:
            mcp_tool = tool.to_mcp_tool()
            name = str(mcp_tool.name)
            if server_keys and not tool_name_allowed_for_servers(name, server_keys):
                continue
            if deny_entries and name != MCP_WIRE_SEARCH_TOOL_NAME:
                from cyt.permissions.match import is_catalog_tool_denied

                if is_catalog_tool_denied(name, deny_entries):
                    continue
            if name == MCP_WIRE_SEARCH_TOOL_NAME:
                refreshed = active_cache.search_tool()
                search_stub = (refreshed or tool).model_copy(update={"output_schema": None})
                stubs.append(search_stub)
                continue
            stub = _stub_from_tool(tool, retain=self._retain)
            stubs.append(stub.model_copy(update={"output_schema": None}))
        if not stubs and input_count > 0 and is_session_scoping_active():
            # Mis-scoped filter (e.g. shared process, wrong CYT_WORKSPACE) — prefer
            # showing tools over returning an empty list to Cursor.
            stubs = []
            deny_entries = self._current_deny_entries()
            for tool in tools:
                mcp_tool = tool.to_mcp_tool()
                name = str(mcp_tool.name)
                if deny_entries and name != MCP_WIRE_SEARCH_TOOL_NAME:
                    from cyt.permissions.match import is_catalog_tool_denied

                    if is_catalog_tool_denied(name, deny_entries):
                        continue
                if name == MCP_WIRE_SEARCH_TOOL_NAME:
                    refreshed = active_cache.search_tool()
                    search_stub = (refreshed or tool).model_copy(update={"output_schema": None})
                    stubs.append(search_stub)
                    continue
                stub = _stub_from_tool(tool, retain=self._retain)
                stubs.append(stub.model_copy(update={"output_schema": None}))
        return stubs


def _tool_from_catalog_entry(entry: dict[str, Any]) -> Tool | None:
    name = str(entry.get("name") or "").strip()
    if not name or name in {MCP_WIRE_SEARCH_TOOL_NAME}:
        return None
    schema = entry.get("inputSchema")
    if not isinstance(schema, dict):
        schema = entry.get("input_schema")
    parameters = dict(schema) if isinstance(schema, dict) else dict(_MINIMAL_OBJECT_SCHEMA)
    description = str(entry.get("description") or "")
    return Tool(name=name, description=description, parameters=parameters)


async def list_stubs_from_runtime_cache(
    cache: RuntimeToolCache,
    config: AggregatorConfig,
    *,
    config_holder: ConfigHolder | None = None,
) -> Sequence[Tool]:
    """Build frontend tools/list stubs from a populated runtime cache (no backend round-trip)."""
    backend_tools: list[Tool] = []
    for entry in cache.snapshot():
        tool = _tool_from_catalog_entry(entry)
        if tool is not None:
            backend_tools.append(tool)
    search_tool = cache.search_tool()
    if search_tool is not None:
        backend_tools.append(search_tool)
    transform = StubListTransform(
        cache,
        retain=config.stub_retain,
        config_holder=config_holder,
    )
    return await transform.list_tools(backend_tools)

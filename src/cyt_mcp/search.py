"""cyt-mcp get-tool-definitions tool: on-demand full backend tool definitions."""

from __future__ import annotations

from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.tools.base import Tool, ToolResult

from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.tool_name_fuzzy import fuzzy_resolve_tool_name

# Canonical name used by hooks, session logs, and tool-gate normalization.
SEARCH_TOOL_NAME = "cyt-mcp_get-tool-definitions"
# MCP stdio wire name. Must not share the cyt-mcp server prefix or Cursor drops it from tools/list.
MCP_WIRE_SEARCH_TOOL_NAME = "get-tool-definitions"

_SEARCH_TOOL_BASE_DESCRIPTION = (
    "Returns the full MCP tool definition for a cyt-mcp backend tool by name. "
    "Use when hook-injected stubs lack properties or metadata you need. "
    "The tool_name argument must be one of the backend tools exposed by this server."
)

_CURSOR_SEARCH_NOTE = (
    " Read `.cursor/rules/cyt-injection.mdc` for pruned relevant tool definitions."
)

_EXCLUDED_LOOKUP_NAMES = frozenset({SEARCH_TOOL_NAME, MCP_WIRE_SEARCH_TOOL_NAME})


def search_tool_description(*, agent: str | None) -> str:
    description = _SEARCH_TOOL_BASE_DESCRIPTION
    if agent == "cursor":
        description = f"{description}{_CURSOR_SEARCH_NOTE}"
    return description


def build_search_input_schema(allowed_names: list[str]) -> dict[str, Any]:
    enum_values = sorted(
        {name for name in allowed_names if name and name not in _EXCLUDED_LOOKUP_NAMES},
    )
    return {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Backend cyt-mcp tool name to look up.",
                "enum": enum_values,
            },
        },
        "required": ["tool_name"],
        "additionalProperties": False,
    }


def backend_tool_names(cache: RuntimeToolCache) -> list[str]:
    names = [
        str(entry.get("name") or "")
        for entry in cache.snapshot()
        if str(entry.get("name") or "").strip()
    ]
    return sorted(name for name in names if name not in _EXCLUDED_LOOKUP_NAMES)


def format_tool_name_usage_message(*, agent: str | None) -> str:
    message = (
        "Use the `tool_name` argument with one of the backend cyt-mcp tools "
        "to get its full definition."
    )
    if agent == "cursor":
        message += " Or read `.cursor/rules/cyt-injection.mdc` if it exists."
    return message


def format_unsupported_parameters_message(
    parameter_names: list[str],
    *,
    agent: str | None,
) -> str:
    quoted = ", ".join(f"`{name}`" for name in parameter_names)
    message = (
        f"Parameter(s) {quoted} are not supported. "
        "Use `tool_name` with one of the backend tools to get their full definitions."
    )
    if agent == "cursor":
        message += " Or read `.cursor/rules/cyt-injection.mdc` if it exists."
    return message


def format_unknown_tool_message(names: list[str]) -> str:
    if not names:
        return "No backend tools are available."
    return "Use one of these tool names:\n" + "\n".join(names)


def parse_get_tool_definitions_arguments(
    arguments: dict[str, Any],
    *,
    agent: str | None,
) -> tuple[str | None, str | None]:
    """Return ``(tool_name, error_message)``. ``error_message`` is set when arguments are invalid."""
    allowed_keys = {"tool_name", "toolName"}
    unexpected = [key for key in arguments if key not in allowed_keys]
    if unexpected:
        return None, format_unsupported_parameters_message(unexpected, agent=agent)

    raw_name = arguments.get("tool_name") or arguments.get("toolName")
    name = str(raw_name or "").strip()
    if not name:
        return None, format_tool_name_usage_message(agent=agent)
    return name, None


def lookup_tool_definition(cache: RuntimeToolCache, tool_name: str) -> dict[str, Any]:
    name = str(tool_name or "").strip()
    if not name:
        raise ValueError("tool_name is required")
    if name in _EXCLUDED_LOOKUP_NAMES:
        raise ValueError(f"{SEARCH_TOOL_NAME} cannot look up itself")
    allowed_names = backend_tool_names(cache)
    allowed = set(allowed_names)
    if name not in allowed:
        resolved = fuzzy_resolve_tool_name(allowed_names, name)
        if resolved is None:
            raise ValueError(format_unknown_tool_message(allowed_names))
        name = resolved
    definition = cache.search_index_entry(name)
    if definition is None:
        raise ValueError(f"tool {name!r} is not available in the search index")
    return dict(definition)


class GetToolDefinitionsTool(Tool):
    """MCP tool that resolves backend definitions with friendly argument errors."""

    def __init__(
        self,
        cache: RuntimeToolCache,
        *,
        agent: str | None,
        parameters: dict[str, Any],
        description: str,
    ) -> None:
        super().__init__(
            name=MCP_WIRE_SEARCH_TOOL_NAME,
            parameters=parameters,
            description=description,
            output_schema=None,
        )
        self._cache = cache
        self._agent = agent

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        tool_name, error = parse_get_tool_definitions_arguments(arguments, agent=self._agent)
        if error is not None:
            return ToolResult(content=error, is_error=True)
        try:
            result = lookup_tool_definition(self._cache, tool_name or "")
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True)
        return self.convert_result(result)


def register_search_tool(
    server: FastMCP,
    cache: RuntimeToolCache,
    *,
    agent: str | None,
) -> Tool:
    allowed = [str(entry.get("name") or "") for entry in cache.snapshot()]
    schema = build_search_input_schema(allowed)
    tool = GetToolDefinitionsTool(
        cache,
        agent=agent,
        parameters=schema,
        description=search_tool_description(agent=agent),
    )
    cast(Any, server).add_tool(tool)
    cache.set_search_tool(tool)
    return tool


def refresh_search_tool_schema(cache: RuntimeToolCache) -> None:
    tool = cache.search_tool()
    if tool is None:
        return
    allowed = [str(entry.get("name") or "") for entry in cache.snapshot()]
    schema = build_search_input_schema(allowed)
    mcp_tool = tool.to_mcp_tool()
    updated = tool.model_copy(
        update={
            "parameters": schema,
            "description": mcp_tool.description or search_tool_description(agent=None),
        },
    )
    cache.set_search_tool(updated)

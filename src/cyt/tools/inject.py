"""Format <agent-tools> injection blocks."""

from __future__ import annotations

import json
from typing import Any

from cyt.indexer.tokens import count_tokens
from cyt.tools.serialize import minimize_json_single_quotes

_AGENT_TOOLS_DESCRIPTION = (
    "Pruned MCP tool definitions below-minimized JSON with relevant properties and enums only for selection. "
    "Name and description live on each <tool> tag; JSON carries input_schema with "
    "outer double quotes swapped by single quotes to save tokens normally should be double quotes."
)

_AGENT_TOOLS_DESCRIPTION_STUBS_ONLY = (
    "Pruned MCP tool definitions below-minimized JSON with relevant properties and enums only for selection. "
    "Name lives on each <tool> tag; descriptions are in root tools[] stubs; JSON carries input_schema with "
    "outer double quotes swapped by single quotes to save tokens normally should be double quotes."
)


def _xml_single_quoted_attr(value: str) -> str:
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    escaped = escaped.replace('\\"', '"')
    return escaped.replace("&", "&amp;").replace("'", "&apos;")


def _agent_tools_open_tag(*, include_tool_description: bool = True) -> str:
    intro = (
        _AGENT_TOOLS_DESCRIPTION
        if include_tool_description
        else _AGENT_TOOLS_DESCRIPTION_STUBS_ONLY
    )
    return f"<agent-tools description='{_xml_single_quoted_attr(intro)}'>"


def ensure_agent_tools_starts_on_new_line(injection: str, *, after: str = "") -> str:
    """Prefix a newline when ``after`` lacks one and injection opens with ``<agent-tools>``."""
    stripped = injection.lstrip("\n")
    if not stripped.startswith("<agent-tools"):
        return injection
    if after and not after.endswith("\n"):
        return "\n" + stripped
    if injection.startswith("\n"):
        return injection
    return "\n" + stripped


def _tool_open_tag(name: str, description: str) -> str:
    attrs = [f"name='{_xml_single_quoted_attr(name)}'"]
    if description:
        attrs.append(f"description='{_xml_single_quoted_attr(description)}'")
    return f"<tool {' '.join(attrs)}>"


def format_tool_item(
    tool: dict[str, Any],
    *,
    include_tool_description: bool = True,
) -> str:
    """Format a single ``<tool>…</tool>`` block (no ``<agent-tools>`` wrapper)."""
    name = str(tool.get("name", "")).strip()
    if not name:
        return ""
    description = ""
    if include_tool_description:
        description = str(tool.get("description", "") or "").strip()
    schema = tool.get("input_schema")
    if schema is None:
        schema = tool.get("parameters", {})
    body = {"input_schema": schema or {}}
    return "\n".join(
        [
            _tool_open_tag(name, description),
            minimize_json_single_quotes(body),
            "</tool>",
        ],
    )


def format_agent_tools(
    pruned_tools: list[dict[str, Any]],
    *,
    include_tool_description: bool = True,
) -> str:
    if not pruned_tools:
        return ""
    item_lines: list[str] = []
    for tool in pruned_tools:
        item = format_tool_item(tool, include_tool_description=include_tool_description)
        if item:
            item_lines.append(item)
    if not item_lines:
        return ""
    lines = [_agent_tools_open_tag(include_tool_description=include_tool_description)]
    lines.extend(item_lines)
    lines.append("</agent-tools>")
    return ensure_agent_tools_starts_on_new_line("\n".join(lines))


def injection_token_count(text: str) -> int:
    if not text.strip():
        return 0
    return count_tokens(text)

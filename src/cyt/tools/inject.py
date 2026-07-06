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


def format_agent_tools(
    pruned_tools: list[dict[str, Any]],
    *,
    include_tool_description: bool = True,
) -> str:
    if not pruned_tools:
        return ""
    lines = [_agent_tools_open_tag(include_tool_description=include_tool_description)]
    for tool in pruned_tools:
        name = str(tool.get("name", "")).strip()
        if not name:
            continue
        description = ""
        if include_tool_description:
            description = str(tool.get("description", "") or "").strip()
        lines.append(_tool_open_tag(name, description))
        schema = tool.get("input_schema")
        if schema is None:
            schema = tool.get("parameters", {})
        body = {"input_schema": schema or {}}
        lines.append(minimize_json_single_quotes(body))
        lines.append("</tool>")
    lines.append("</agent-tools>")
    return ensure_agent_tools_starts_on_new_line("\n".join(lines))


def injection_token_count(text: str) -> int:
    if not text.strip():
        return 0
    return count_tokens(text)

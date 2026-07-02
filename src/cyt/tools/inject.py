"""Format <agent-tools> injection blocks."""

from __future__ import annotations

import json
from typing import Any

from cyt.indexer.tokens import count_tokens
from cyt.tools.serialize import minimize_json_single_quotes

_INTRO = (
    "Pruned MCP tool definitions below—minimized JSON for selection. "
    "Name and description live on each <tool> tag; JSON carries input_schema with "
    "outer double quotes swapped by single quotes to save tokens normally should be double quotes."
)


def _xml_single_quoted_attr(value: str) -> str:
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    escaped = escaped.replace('\\"', '"')
    return escaped.replace("&", "&amp;").replace("'", "&apos;")


def _tool_open_tag(name: str, description: str) -> str:
    attrs = [f"name='{_xml_single_quoted_attr(name)}'"]
    if description:
        attrs.append(f"description='{_xml_single_quoted_attr(description)}'")
    return f"<tool {' '.join(attrs)}>"


def format_agent_tools(pruned_tools: list[dict[str, Any]]) -> str:
    if not pruned_tools:
        return ""
    lines = [_INTRO, "", "<agent-tools>"]
    for tool in pruned_tools:
        name = str(tool.get("name", "")).strip()
        if not name:
            continue
        description = str(tool.get("description", "") or "").strip()
        lines.append(_tool_open_tag(name, description))
        body = {"input_schema": tool.get("input_schema", {})}
        lines.append(minimize_json_single_quotes(body))
        lines.append("</tool>")
    lines.append("</agent-tools>")
    return "\n".join(lines)


def injection_token_count(text: str) -> int:
    if not text.strip():
        return 0
    return count_tokens(text)

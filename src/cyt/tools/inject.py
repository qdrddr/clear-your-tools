"""Format <agent-tools> injection blocks."""

from __future__ import annotations

from typing import Any

from cyt.indexer.tokens import count_tokens
from cyt.tools.serialize import minimize_json_single_quotes

_INTRO = (
    "Based on the user query, pruned MCP tool definitions are listed below. "
    "Each tool entry is minimized JSON suitable for tool selection context."
)


def format_agent_tools(pruned_tools: list[dict[str, Any]]) -> str:
    if not pruned_tools:
        return ""
    lines = [_INTRO, "", "<agent-tools>"]
    for tool in pruned_tools:
        name = str(tool.get("name", "")).strip()
        if not name:
            continue
        lines.append(f'<tool name="{name}">')
        lines.append(minimize_json_single_quotes(tool))
        lines.append("</tool>")
    lines.append("</agent-tools>")
    return "\n".join(lines)


def injection_token_count(text: str) -> int:
    if not text.strip():
        return 0
    return count_tokens(text)

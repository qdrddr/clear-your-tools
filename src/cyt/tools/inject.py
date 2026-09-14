"""Format <agent-tools> injection blocks."""

from __future__ import annotations

import json
from typing import Any

from cyt.executor.tool_names import agent_visible_tool_name
from cyt.indexer.tokens import count_tokens
from cyt.tools.serialize import format_examples_block, minimize_json_single_quotes

_EXECUTOR_WORKSPACE_NOTE = (
    "When using tools with executor, you typically required to specify path to the repository "
    "with the current project's workspace_roots"
)

_AGENT_TOOLS_DESCRIPTION_BASE = (
    "Pruned MCP tool definitions below-minimized JSON with relevant properties and enums only for selection. "
    "Name and description live on each <tool> tag; JSON carries input_schema with "
    "outer double quotes swapped by single quotes to save tokens normally should be double quotes. "
    "When available, successful past invocations from this project appear in a per-tool <examples> block."
)

_AGENT_TOOLS_DESCRIPTION_STUBS_ONLY_BASE = (
    "Pruned MCP tool definitions below-minimized JSON with relevant properties and enums only for selection. "
    "Name lives on each <tool> tag; descriptions are in root tools[] stubs; JSON carries input_schema with "
    "outer double quotes swapped by single quotes to save tokens normally should be double quotes. "
    "When available, successful past invocations from this project appear in a per-tool <examples> block."
)


def _agent_tools_description(
    *,
    include_tool_description: bool,
    include_executor_workspace_note: bool,
) -> str:
    intro = (
        _AGENT_TOOLS_DESCRIPTION_BASE
        if include_tool_description
        else _AGENT_TOOLS_DESCRIPTION_STUBS_ONLY_BASE
    )
    if include_executor_workspace_note:
        return f"{intro} {_EXECUTOR_WORKSPACE_NOTE}."
    return intro


def _xml_single_quoted_attr(value: str) -> str:
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    escaped = escaped.replace('\\"', '"')
    return escaped.replace("&", "&amp;").replace("'", "&apos;")


def _agent_tools_open_tag(
    *,
    workspace_paths: list[str] | None = None,
) -> str:
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    if len(paths) == 1:
        return f"<agent-tools path='{_xml_single_quoted_attr(paths[0])}'>"
    return "<agent-tools>"


def _format_workspace_roots_block(workspace_paths: list[str]) -> str:
    items = [
        f"<item path='{_xml_single_quoted_attr(path.strip())}'/>"
        for path in workspace_paths
        if path.strip()
    ]
    if not items:
        return ""
    return "\n".join(["<workspace_roots>", *items, "</workspace_roots>"])


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


def _tool_open_tag(name: str, description: str, *, tier: str | None = None) -> str:
    attrs = [f"name='{_xml_single_quoted_attr(name)}'"]
    if description:
        attrs.append(f"description='{_xml_single_quoted_attr(description)}'")
    if tier:
        attrs.append(f"tier='{_xml_single_quoted_attr(tier)}'")
    return f"<tool {' '.join(attrs)}>"


def _tool_input_schema(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("input_schema")
    if schema is None:
        schema = tool.get("parameters")
    return dict(schema) if isinstance(schema, dict) else {}


def _tool_injection_examples(tool: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    raw = tool.get("cyt_injection_examples")
    if not isinstance(raw, list) or not raw:
        return [], None
    examples = [item for item in raw if isinstance(item, dict)]
    if not examples:
        return [], None
    max_chars = tool.get("cyt_injection_examples_max_chars")
    if isinstance(max_chars, int) and max_chars > 0:
        return examples, max_chars
    return examples, None


def _format_examples_block(tool: dict[str, Any]) -> str:
    examples, max_chars = _tool_injection_examples(tool)
    return format_examples_block(examples, max_chars=max_chars)


def format_tool_item(
    tool: dict[str, Any],
    *,
    include_tool_description: bool = True,
) -> str:
    """Format a single ``<tool>…</tool>`` block (no ``<agent-tools>`` wrapper)."""
    name = agent_visible_tool_name(tool)
    if not name:
        return ""
    description = ""
    if include_tool_description:
        description = str(tool.get("description", "") or "").strip()
    schema = _tool_input_schema(tool)
    tier = tool.get("cyt_injection_tier")
    tier_attr = str(tier).strip().lower() if isinstance(tier, str) and tier.strip() else None
    lines = [_tool_open_tag(name, description, tier=tier_attr)]
    if schema:
        lines.append(minimize_json_single_quotes({"input_schema": schema}))
    if examples_block := _format_examples_block(tool):
        lines.append(examples_block)
    lines.append("</tool>")
    return "\n".join(lines)


def format_agent_tools(
    pruned_tools: list[dict[str, Any]],
    *,
    include_tool_description: bool = True,
    include_executor_workspace_note: bool = False,
    workspace_paths: list[str] | None = None,
    session_text: str = "",
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
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    intro = _agent_tools_description(
        include_tool_description=include_tool_description,
        include_executor_workspace_note=include_executor_workspace_note,
    )
    lines = [_agent_tools_open_tag(workspace_paths=paths)]
    from cyt.injection.header_pre_exposed import agent_tools_intro_pre_exposed

    if not agent_tools_intro_pre_exposed(session_text, intro):
        lines.append(intro)
    if len(paths) > 1:
        roots_block = _format_workspace_roots_block(paths)
        if roots_block:
            lines.append(roots_block)
    lines.extend(item_lines)
    lines.append("</agent-tools>")
    return ensure_agent_tools_starts_on_new_line("\n".join(lines))


def injection_token_count(text: str) -> int:
    if not text.strip():
        return 0
    return count_tokens(text)

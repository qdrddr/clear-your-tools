"""Format <agent-tools> injection blocks."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from cyt.executor.tool_names import agent_visible_tool_name
from cyt.indexer.tokens import count_tokens
from cyt.tiers.tool_token_materialization import input_schema_from_tool
from cyt.tools.injection_schema import entangle_examples_with_schema
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
    session_text: str = "",
) -> str:
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    if len(paths) == 1:
        from cyt.injection.header_pre_exposed import agent_tools_path_pre_exposed

        if not agent_tools_path_pre_exposed(session_text, paths[0]):
            return f"<agent-tools path='{_xml_single_quoted_attr(paths[0])}'>"
    return "<agent-tools>"


def _finalize_agent_tools_block(lines: list[str]) -> str:
    """Join agent-tools lines; return empty string when only the wrapper would remain."""
    if len(lines) < 2 or lines[-1] != "</agent-tools>":
        return ensure_agent_tools_starts_on_new_line("\n".join(lines))
    inner = "\n".join(lines[1:-1]).strip()
    if not inner:
        return ""
    return ensure_agent_tools_starts_on_new_line("\n".join(lines))


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


TIER_GROUP_ORDER: tuple[str, ...] = ("t4", "t3", "t2", "t1", "t0")


def _tool_open_tag(name: str, description: str) -> str:
    attrs = [f"name='{_xml_single_quoted_attr(name)}'"]
    if description:
        attrs.append(f"description='{_xml_single_quoted_attr(description)}'")
    return f"<tool {' '.join(attrs)}>"


def injection_tier_for_tool(tool: dict[str, Any]) -> str | None:
    """Return ``cyt_injection_tier`` (t0-t4) stamped by the tier manager for grouping."""
    tier = tool.get("cyt_injection_tier")
    if not isinstance(tier, str) or not tier.strip():
        return None
    label = tier.strip().lower()
    if label in TIER_GROUP_ORDER:
        return label
    return None


def tier_wrapper_tag(tier: str) -> str:
    """XML wrapper tag for a tier bucket, e.g. ``tier_t3`` → ``<tier_t3>…</tier_t3>``."""
    label = str(tier or "").strip().lower()
    return f"tier_{label}" if label else "tier"


def _format_tier_group(tier: str, items: list[str]) -> str:
    tag = tier_wrapper_tag(tier)
    body = "\n".join(items)
    return f"<{tag}>\n{body}\n</{tag}>"


def format_tools_grouped_by_tier(
    tools: list[dict[str, Any]],
    *,
    include_tool_description: bool = True,
    format_item: Callable[..., str] | None = None,
) -> str:
    """Format tools wrapped in ``<tier_tN>`` groups (t0-t4 only; skip tools without a tier)."""
    render = format_item or format_tool_item
    buckets: OrderedDict[str, list[str]] = OrderedDict(
        (tier, []) for tier in TIER_GROUP_ORDER
    )

    for tool in tools:
        tier = injection_tier_for_tool(tool)
        if tier is None:
            continue
        item = render(tool, include_tool_description=include_tool_description)
        if not item:
            continue
        buckets[tier].append(item)

    blocks: list[str] = []
    for tier in TIER_GROUP_ORDER:
        items = buckets[tier]
        if items:
            blocks.append(_format_tier_group(tier, items))
    return "\n".join(blocks)


def _tool_input_schema(tool: dict[str, Any]) -> dict[str, Any]:
    return input_schema_from_tool(tool)


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
    schema = _tool_input_schema(tool)
    if schema:
        examples = entangle_examples_with_schema(examples, schema)
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
    lines = [_tool_open_tag(name, description)]
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
    body = format_tools_grouped_by_tier(
        pruned_tools,
        include_tool_description=include_tool_description,
    )
    if not body.strip():
        return ""
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    intro = _agent_tools_description(
        include_tool_description=include_tool_description,
        include_executor_workspace_note=include_executor_workspace_note,
    )
    lines = [_agent_tools_open_tag(workspace_paths=paths, session_text=session_text)]
    from cyt.injection.header_pre_exposed import agent_tools_intro_pre_exposed

    if not agent_tools_intro_pre_exposed(session_text, intro):
        lines.append(intro)
    if len(paths) > 1:
        roots_block = _format_workspace_roots_block(paths)
        if roots_block:
            lines.append(roots_block)
    lines.append(body)
    lines.append("</agent-tools>")
    return _finalize_agent_tools_block(lines)


def injection_token_count(text: str) -> int:
    if not text.strip():
        return 0
    return count_tokens(text)

"""Format MCPC ``<agent-tools>`` injection blocks."""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

from cyt.tools.inject import _xml_single_quoted_attr, ensure_agent_tools_starts_on_new_line
from cyt.tools.serialize import minimize_json_single_quotes

_MCPC_WORKSPACE_NOTE = (
    "When using tools with mcpc CLI app, you typically required to specify path to the repository "
    "with the current project's workspace_roots/path"
)

_AGENT_TOOLS_DESCRIPTION = (
    "Pruned MCP tool definitions below-minimized JSON with relevant properties and enums only for selection. "
    "Name and description live on each <tool> tag; JSON carries input_schema with "
    "outer double quotes swapped by single quotes to save tokens normally should be double quotes."
)


def _mcpc_agent_tools_description(*, include_workspace_note: bool) -> str:
    if include_workspace_note:
        return f"{_AGENT_TOOLS_DESCRIPTION} {_MCPC_WORKSPACE_NOTE}."
    return f"{_AGENT_TOOLS_DESCRIPTION}."


def _agent_tools_open_tag(
    *,
    workspace_paths: list[str] | None = None,
) -> str:
    intro = _mcpc_agent_tools_description(include_workspace_note=True)
    attrs = [f"description='{_xml_single_quoted_attr(intro)}'"]
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    if len(paths) == 1:
        attrs.append(f"path='{_xml_single_quoted_attr(paths[0])}'")
    elif paths:
        attrs.append(f"path='{_xml_single_quoted_attr(paths[0])}'")
    return f"<agent-tools {' '.join(attrs)}>"


def _cli_example(session: str, tool_name: str, input_schema: dict[str, Any]) -> str:
    properties = input_schema.get("properties")
    args: dict[str, str] = {}
    if isinstance(properties, dict):
        for prop_name, spec in properties.items():
            if not isinstance(spec, dict):
                args[str(prop_name)] = "string"
                continue
            prop_type = str(spec.get("type") or "string")
            if prop_type == "string":
                args[str(prop_name)] = "string"
            else:
                args[str(prop_name)] = f"type: {prop_type}"
    payload = json.dumps(args, separators=(",", ":"), ensure_ascii=False)
    return f"echo '{payload}' | mcpc {session} tools-call {tool_name}"


def _format_mcpc_tool_item(tool: dict[str, Any]) -> str:
    tool_name = str(tool.get("tool_name") or tool.get("name") or "").strip()
    session = str(tool.get("mcpc_session") or "").strip()
    if not tool_name or not session:
        return ""
    title = str(tool.get("title") or tool_name).strip()
    description = str(tool.get("description") or "").strip()
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        schema = tool.get("parameters") if isinstance(tool.get("parameters"), dict) else {}
    attrs = [
        f"name='{_xml_single_quoted_attr(tool_name)}'",
        f"title='{_xml_single_quoted_attr(title)}'",
        f"mcpc-session='{_xml_single_quoted_attr(session)}'",
    ]
    if description:
        attrs.append(f"description='{_xml_single_quoted_attr(description)}'")
    cli_line = _cli_example(session, tool_name, schema or {})
    body = {"input_schema": schema or {}}
    return "\n".join(
        [
            f"<tool {' '.join(attrs)}>",
            "<cli>",
            cli_line,
            "</cli>",
            "<json-schema>",
            minimize_json_single_quotes(body),
            "</json-schema>",
            "</tool>",
        ],
    )


def _group_tools_by_session(tools: list[dict[str, Any]]) -> OrderedDict[str, list[dict[str, Any]]]:
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for tool in tools:
        session = str(tool.get("mcpc_session") or "").strip()
        if not session:
            continue
        grouped.setdefault(session, []).append(tool)
    return grouped


def _format_server_block(session: str, tools: list[dict[str, Any]]) -> str:
    if not tools:
        return ""
    first = tools[0]
    server_name = str(first.get("server_name") or session).strip()
    instructions = str(first.get("server_instructions") or "").strip()
    attrs = [f"name='{_xml_single_quoted_attr(server_name)}'"]
    if instructions:
        attrs.append(f"instructions='{_xml_single_quoted_attr(instructions)}'")
    item_lines = [_format_mcpc_tool_item(tool) for tool in tools]
    item_lines = [line for line in item_lines if line]
    if not item_lines:
        return ""
    return "\n".join([f"<server {' '.join(attrs)}>", *item_lines, "</server>"])


def format_mcpc_agent_tools(
    pruned_tools: list[dict[str, Any]],
    *,
    workspace_paths: list[str] | None = None,
) -> str:
    if not pruned_tools:
        return ""
    grouped = _group_tools_by_session(pruned_tools)
    server_blocks = [_format_server_block(session, tools) for session, tools in grouped.items()]
    server_blocks = [block for block in server_blocks if block]
    if not server_blocks:
        return ""
    lines = [
        _agent_tools_open_tag(workspace_paths=workspace_paths),
        *server_blocks,
        "</agent-tools>",
    ]
    return ensure_agent_tools_starts_on_new_line("\n".join(lines))

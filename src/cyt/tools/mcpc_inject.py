"""Format MCPC ``<agent-tools>`` injection blocks."""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, cast

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


def _tool_json_schema_body(tool: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    raw_schema = tool.get("input_schema")
    if isinstance(raw_schema, dict):
        schema = raw_schema
    else:
        raw_input_schema = tool.get("inputSchema")
        if isinstance(raw_input_schema, dict):
            schema = raw_input_schema
        else:
            raw_parameters = tool.get("parameters")
            if isinstance(raw_parameters, dict):
                schema = raw_parameters
    body = dict(schema)
    annotations = tool.get("annotations")
    if isinstance(annotations, dict):
        body["annotations"] = annotations
    execution = tool.get("execution")
    if isinstance(execution, dict):
        body["execution"] = execution
    return body


def _input_schema_only(schema_body: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in schema_body.items() if key not in ("annotations", "execution")
    }


def _resolve_schema_type(spec: dict[str, Any]) -> str:
    raw_type = spec.get("type")
    if isinstance(raw_type, list):
        for item in raw_type:
            if item != "null":
                return str(item)
        return "string"
    if isinstance(raw_type, str):
        return raw_type
    if isinstance(spec.get("properties"), dict):
        return "object"
    if "items" in spec:
        return "array"
    return "string"


def _property_names(spec: dict[str, Any]) -> list[str]:
    properties = spec.get("properties")
    if not isinstance(properties, dict):
        return []
    required = spec.get("required")
    if isinstance(required, list) and required:
        return [str(name) for name in required if name in properties]
    return [str(name) for name in properties]


def _array_example_count(spec: dict[str, Any]) -> int:
    min_items = spec.get("minItems", 1)
    if not isinstance(min_items, int) or min_items < 1:
        min_items = 1
    max_items = spec.get("maxItems")
    if isinstance(max_items, int):
        return min(min_items, max_items)
    return min_items


def _example_from_variant_specs(spec: dict[str, Any]) -> object | None:
    for key in ("anyOf", "oneOf"):
        variants = spec.get(key)
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict):
                    return _example_from_schema_spec(variant)
    return None


def _example_object_from_schema(spec: dict[str, Any]) -> dict[str, object]:
    properties = spec.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {
        name: _example_from_schema_spec(properties[name])
        for name in _property_names(spec)
        if isinstance(properties.get(name), dict)
    }


def _example_array_from_schema(spec: dict[str, Any]) -> list[object]:
    items = spec.get("items")
    if not isinstance(items, dict):
        return []
    count = _array_example_count(spec)
    return [_example_from_schema_spec(items) for _ in range(count)]


def _example_primitive_from_schema(schema_type: str) -> object:
    if schema_type == "boolean":
        return False
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0
    if schema_type == "string":
        return "string"
    if schema_type == "null":
        return None
    return f"type: {schema_type}"


def _example_from_schema_spec(spec: dict[str, Any]) -> object:
    if "default" in spec:
        return spec["default"]

    enum = spec.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]

    variant = _example_from_variant_specs(spec)
    if variant is not None:
        return variant

    schema_type = _resolve_schema_type(spec)
    if schema_type == "object":
        return _example_object_from_schema(spec)
    if schema_type == "array":
        return _example_array_from_schema(spec)
    return _example_primitive_from_schema(schema_type)


def _cli_payload_from_input_schema(input_schema: dict[str, Any]) -> dict[str, object]:
    example = _example_from_schema_spec(input_schema)
    if isinstance(example, dict):
        return cast(dict[str, object], example)
    return {}


def _cli_example(session: str, tool_name: str, input_schema: dict[str, Any]) -> str:
    payload = _cli_payload_from_input_schema(_input_schema_only(input_schema))
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return f"echo '{encoded}' | mcpc {session} tools-call {tool_name}"


def _format_mcpc_tool_item(tool: dict[str, Any]) -> str:
    tool_name = str(tool.get("tool_name") or tool.get("name") or "").strip()
    session = str(tool.get("mcpc_session") or "").strip()
    if not tool_name or not session:
        return ""
    title = str(tool.get("title") or tool_name).strip()
    description = str(tool.get("description") or "").strip()
    schema = _tool_json_schema_body(tool)
    attrs = [
        f"name='{_xml_single_quoted_attr(tool_name)}'",
        f"title='{_xml_single_quoted_attr(title)}'",
        f"mcpc-session='{_xml_single_quoted_attr(session)}'",
    ]
    if description:
        attrs.append(f"description='{_xml_single_quoted_attr(description)}'")
    cli_line = _cli_example(session, tool_name, schema)
    return "\n".join(
        [
            f"<tool {' '.join(attrs)}>",
            "<cli>",
            cli_line,
            "</cli>",
            "<json-schema>",
            minimize_json_single_quotes(schema),
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

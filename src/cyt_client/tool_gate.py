"""Pre-toolcall validation against session injection logs (stdlib only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cyt_client.agent import infer_harness_agent
from cyt_client.sessions import read_session_log_file, session_log_path

_PRE_TOOL_EVENTS = frozenset(
    {
        "preToolUse",
        "PreToolUse",
    },
)

_CYT_MCP_GET_TOOL_DEFINITIONS_TOOL = "cyt-mcp_get-tool-definitions"
_CYT_MCP_SERVER_NAMES = frozenset({"cyt-mcp", "cyt_mcp"})


def is_pre_tool_event(payload: dict[str, Any]) -> bool:
    for layer in (
        payload,
        payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    ):
        if not isinstance(layer, dict):
            continue
        event = layer.get("hook_event_name") or layer.get("hookEventName")
        if isinstance(event, str) and event.strip() in _PRE_TOOL_EVENTS:
            return True
    return False


def _payload_layers(data: dict[str, Any]) -> list[dict[str, Any]]:
    layers = [data]
    nested = data.get("payload")
    if isinstance(nested, dict):
        layers.append(nested)
    return layers


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for layer in _payload_layers(data):
        for key in keys:
            raw = layer.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return None


def normalize_mcp_tool_name(raw_name: str, *, agent: str | None) -> str:
    name = str(raw_name or "").strip()
    if not name:
        return ""
    if name.upper().startswith("MCP:"):
        name = name[4:].strip()
    if name.startswith("mcp__"):
        parts = [part for part in name.split("__") if part]
        if len(parts) >= 3 and parts[0] == "mcp":
            server = parts[1]
            tool = "__".join(parts[2:])
            return f"{server}_{tool}"
        if len(parts) == 2 and parts[0] == "mcp":
            return parts[1]
    if agent == "codex" and name.count("__") >= 2 and name.startswith("mcp__"):
        _, server, tool = name.split("__", 2)
        if server and tool:
            return f"{server}_{tool}"
    if name == "get-tool-definitions":
        return _CYT_MCP_GET_TOOL_DEFINITIONS_TOOL
    return name


def _parse_tool_args_from_layer(layer: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("tool_input", "toolInput", "arguments", "args", "input"):
        raw = layer.get(key)
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def _extract_tool_args(payload: dict[str, Any]) -> dict[str, Any] | None:
    for layer in _payload_layers(payload):
        args = _parse_tool_args_from_layer(layer)
        if args is not None:
            return args
    return None


def _extract_tool_call(payload: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    agent = infer_harness_agent(payload)
    tool_name = _first_str(
        payload,
        "tool_name",
        "toolName",
        "tool",
        "name",
    )
    if tool_name is None:
        tool_input = payload.get("tool_input")
        if isinstance(tool_input, dict):
            nested_name = tool_input.get("name") or tool_input.get("tool_name")
            if isinstance(nested_name, str):
                tool_name = nested_name
    if tool_name is None:
        return None, None

    normalized = normalize_mcp_tool_name(tool_name, agent=agent)
    return normalized or None, _extract_tool_args(payload)


def is_cyt_mcp_get_tool_definitions_tool(tool_name: str, *, agent: str | None = None) -> bool:
    return normalize_mcp_tool_name(tool_name, agent=agent) == _CYT_MCP_GET_TOOL_DEFINITIONS_TOOL


def is_cyt_mcp_search_tool(tool_name: str, *, agent: str | None = None) -> bool:
    """Deprecated alias for :func:`is_cyt_mcp_get_tool_definitions_tool`."""
    return is_cyt_mcp_get_tool_definitions_tool(tool_name, agent=agent)


def _validate_get_tool_definitions_args(args: dict[str, Any] | None) -> str | None:
    if args is None:
        return "tool_name is required"
    nested = args.get("tool_name") or args.get("toolName")
    if not isinstance(nested, str) or not nested.strip():
        return "tool_name is required"
    return None


def _raw_routes_through_cyt_mcp_server(raw_name: str) -> bool:
    name = raw_name.strip()
    if not name:
        return False
    if name.startswith("mcp__"):
        parts = [part for part in name.split("__") if part]
        if len(parts) >= 2 and parts[1] in _CYT_MCP_SERVER_NAMES:
            return True
    if name.upper().startswith("MCP:"):
        rest = name[4:].strip()
        server_part = rest.split("_", 1)[0]
        if server_part in _CYT_MCP_SERVER_NAMES or rest.startswith("cyt-mcp"):
            return True
    return False


def _shares_server_prefix(tool_name: str, other: str) -> bool:
    if "_" not in tool_name or "_" not in other:
        return False
    return tool_name.split("_", 1)[0] == other.split("_", 1)[0]


def _cyt_mcp_tool_names_from_session(path: Path) -> set[str]:
    _agent, entries = read_session_log_file(path)
    names: set[str] = set()
    for entry in entries:
        if entry.get("kind") != "tool":
            continue
        key = str(entry.get("key") or "")
        catalog = str(entry.get("catalog") or "")
        if catalog != "cyt_mcp" and not key.startswith("tool:cyt_mcp:"):
            continue
        name = str(entry.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def is_cyt_mcp_gated_tool(
    tool_name: str,
    payload: dict[str, Any],
    *,
    agent: str | None = None,
) -> bool:
    """Return True when pre-tool validation should apply cyt-mcp session rules."""
    if is_cyt_mcp_get_tool_definitions_tool(tool_name, agent=agent):
        return True
    if "-mcp_" in tool_name:
        return True
    raw = _first_str(payload, "tool_name", "toolName", "tool", "name") or tool_name
    if _raw_routes_through_cyt_mcp_server(raw):
        return True
    path = session_log_path(payload)
    if path is not None and path.is_file():
        for session_name in _cyt_mcp_tool_names_from_session(path):
            if _shares_server_prefix(tool_name, session_name):
                return True
    return False


def _allowed_tools_from_session(path: Path) -> dict[str, dict[str, Any]]:
    _agent, entries = read_session_log_file(path)
    allowed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("kind") != "tool":
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        schema = entry.get("input_schema")
        if not isinstance(schema, dict):
            schema = {}
        allowed[name] = schema
    return allowed


def _enum_violation(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    for key, value in args.items():
        prop = properties.get(key)
        if not isinstance(prop, dict):
            continue
        enum_values = prop.get("enum")
        if not isinstance(enum_values, list) or not enum_values:
            continue
        if value not in enum_values:
            return f"property {key!r} value {value!r} not in allowed enum"
    return None


def _property_violation(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    for key in args:
        if key not in properties:
            return f"unknown property {key!r}"
    return None


def _validate_cyt_mcp_session_tool(
    tool_name: str,
    args: dict[str, Any] | None,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    path = session_log_path(payload)
    if path is None or not path.is_file():
        return False, f"tool {tool_name!r} was not injected in this session"

    allowed = _allowed_tools_from_session(path)
    if not allowed:
        return False, f"tool {tool_name!r} was not injected in this session"

    schema = allowed.get(tool_name)
    if schema is None:
        return False, f"tool {tool_name!r} was not injected in this session"

    if args is None:
        return True, ""

    prop_error = _property_violation(schema, args)
    if prop_error:
        return False, prop_error
    enum_error = _enum_violation(schema, args)
    if enum_error:
        return False, enum_error
    return True, ""


def validate_pre_tool_call(payload: dict[str, Any]) -> tuple[bool, str]:
    """Return (allow, reason). Only cyt-mcp tools are gated against the session log."""
    tool_name, args = _extract_tool_call(payload)
    if not tool_name:
        return True, ""

    agent = infer_harness_agent(payload)
    if not is_cyt_mcp_gated_tool(tool_name, payload, agent=agent):
        return True, ""

    if is_cyt_mcp_get_tool_definitions_tool(tool_name, agent=agent):
        error = _validate_get_tool_definitions_args(args)
        if error:
            return False, error
        return True, ""

    return _validate_cyt_mcp_session_tool(tool_name, args, payload)


def format_cursor_deny(reason: str) -> str:
    return json.dumps({"permission": "deny", "user_message": reason})


def format_codex_deny(reason: str) -> str:
    return json.dumps(
        {
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    )


def format_claude_deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", "reason": reason}})

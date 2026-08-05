"""Rewrite tool_gate.py with Type-2 catalog validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cyt_client.agent import infer_harness_agent
from cyt_client.mcpc_shell import parse_mcpc_shell_command
from cyt_client.schema_validate import validate_json_schema
from cyt_client.sessions import (
    read_latest_tool_catalogs,
    read_tools_inject_enabled,
    session_log_path,
)

_GATED_CATALOGS = frozenset({"mcpc", "cyt_mcp", "definitions"})
_PRE_TOOL_EVENTS = frozenset({"preToolUse", "PreToolUse"})
_CYT_MCP_GET_TOOL_DEFINITIONS_TOOL = "cyt-mcp_get-tool-definitions"
_CYT_MCP_SERVER_NAMES = frozenset({"cyt-mcp", "cyt_mcp"})
_SHELL_TOOL_NAMES = frozenset({"Shell", "shell", "Bash", "bash"})


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


def _extract_shell_command(payload: dict[str, Any]) -> str | None:
    args = _extract_tool_args(payload)
    if args is not None:
        for key in ("command", "cmd"):
            raw = args.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return _first_str(payload, "command", "cmd")


def _extract_tool_call(payload: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    agent = infer_harness_agent(payload)
    tool_name = _first_str(payload, "tool_name", "toolName", "tool", "name")
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
    return is_cyt_mcp_get_tool_definitions_tool(tool_name, agent=agent)


def _cyt_mcp_tool_names_from_catalog(catalogs: dict[str, dict[str, Any]]) -> set[str]:
    entry = catalogs.get("tool_catalog:cyt_mcp")
    if entry is None:
        return set()
    tools = entry.get("tools")
    if not isinstance(tools, list):
        return set()
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _cyt_mcp_tool_names_from_session(path: Path) -> set[str]:
    return _cyt_mcp_tool_names_from_catalog(read_latest_tool_catalogs(path))


def is_cyt_mcp_gated_tool(
    tool_name: str,
    payload: dict[str, Any],
    *,
    agent: str | None = None,
) -> bool:
    """Return True when the tool name appears in the Type-2 cyt_mcp catalog."""
    if is_cyt_mcp_get_tool_definitions_tool(tool_name, agent=agent):
        return True
    normalized = normalize_mcp_tool_name(tool_name, agent=agent)
    path = session_log_path(payload)
    if path is not None and path.is_file():
        names = _cyt_mcp_tool_names_from_session(path)
        if normalized in names:
            return True
    return False


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


def _format_tool_definition_for_deny(tool: dict[str, Any]) -> str:
    return json.dumps(tool, indent=2, ensure_ascii=False)


def _deny_message_cyt_mcp(tool_name: str, tool: dict[str, Any], schema_error: str) -> str:
    definition = {
        "name": tool.get("name", tool_name),
        "description": tool.get("description"),
        "input_schema": tool.get("input_schema") or {},
    }
    return (
        f"Hallucinated cyt-mcp tool call for {tool_name!r}: {schema_error}\n\n"
        f"Correct tool definition:\n{_format_tool_definition_for_deny(definition)}\n\n"
        f"Use MCP tool `get-tool-definitions` with arguments: "
        f'{{"tool_name": "{tool.get("name", tool_name)}"}}'
    )


def _deny_message_mcpc(
    session: str,
    tool_name: str,
    tool: dict[str, Any],
    schema_error: str,
) -> str:
    definition = {
        "name": tool.get("name", tool_name),
        "mcpc_session": session,
        "description": tool.get("description"),
        "input_schema": tool.get("input_schema") or {},
    }
    return (
        f"Hallucinated mcpc Shell call for {session} tools-call {tool_name!r}: {schema_error}\n\n"
        f"Correct tool definition:\n{_format_tool_definition_for_deny(definition)}\n\n"
        "Fix the Shell JSON payload to match the mcpc Type-2 schema for this session and tool."
    )


def _deny_message_definitions(tool_name: str, tool: dict[str, Any], schema_error: str) -> str:
    definition = {
        "name": tool.get("name", tool_name),
        "description": tool.get("description"),
        "input_schema": tool.get("input_schema") or {},
    }
    return (
        f"Hallucinated definitions tool call for {tool_name!r}: {schema_error}\n\n"
        f"Correct tool definition:\n{_format_tool_definition_for_deny(definition)}\n\n"
        "Align arguments with the definitions Type-2 schema."
    )


def _find_tool_in_catalog(
    catalogs: dict[str, dict[str, Any]],
    catalog: str,
    tool_name: str,
    *,
    mcpc_session: str | None = None,
) -> dict[str, Any] | None:
    entry = catalogs.get(f"tool_catalog:{catalog}")
    if entry is None:
        return None
    tools = entry.get("tools")
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if name != tool_name:
            continue
        if catalog == "mcpc" and mcpc_session:
            session = str(tool.get("mcpc_session") or "").strip()
            if session and session != mcpc_session:
                continue
        return tool
    return None


def _resolve_catalog_for_mcp_tool(
    tool_name: str,
    catalogs: dict[str, dict[str, Any]],
) -> str | None:
    for catalog in ("cyt_mcp", "definitions"):
        if _find_tool_in_catalog(catalogs, catalog, tool_name) is not None:
            return catalog
    return None


def _shares_cyt_mcp_proxy_prefix(tool_name: str, catalogs: dict[str, dict[str, Any]]) -> bool:
    entry = catalogs.get("tool_catalog:cyt_mcp")
    if entry is None:
        return False
    tools = entry.get("tools")
    if not isinstance(tools, list) or "_" not in tool_name:
        return False
    prefix = tool_name.split("_", 1)[0]
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "")
        if "_" in name and name.split("_", 1)[0] == prefix:
            return True
    return False


def _load_gate_context(
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool | None, bool]:
    path = session_log_path(payload)
    catalogs: dict[str, dict[str, Any]] = {}
    inject_enabled: bool | None = None
    if path is not None and path.is_file():
        catalogs = read_latest_tool_catalogs(path)
        inject_enabled = read_tools_inject_enabled(path)
    strict_gate = inject_enabled is True or bool(catalogs)
    return catalogs, inject_enabled, strict_gate


def _validate_mcpc_shell_pre_tool_call(
    payload: dict[str, Any],
    *,
    catalogs: dict[str, dict[str, Any]],
    strict_gate: bool,
    shell_command: str,
) -> tuple[bool, str] | None:
    mcpc_call = parse_mcpc_shell_command(shell_command)
    if mcpc_call is None:
        return True, ""
    if strict_gate and not catalogs.get("tool_catalog:mcpc"):
        return False, (
            "Type-2 mcpc catalog missing while tools inject is active; "
            "cannot validate mcpc Shell command"
        )
    tool = _find_tool_in_catalog(
        catalogs,
        "mcpc",
        mcpc_call.tool_name,
        mcpc_session=mcpc_call.session,
    )
    if tool is None:
        return False, _deny_message_mcpc(
            mcpc_call.session,
            mcpc_call.tool_name,
            {"name": mcpc_call.tool_name, "input_schema": {}},
            "tool not in mcpc Type-2 catalog for this session",
        )
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        schema = {}
    ok, reason = validate_json_schema(mcpc_call.args, schema)
    if not ok:
        return False, _deny_message_mcpc(
            mcpc_call.session,
            mcpc_call.tool_name,
            tool,
            reason,
        )
    return True, ""


def _validate_catalog_tool_pre_tool_call(
    payload: dict[str, Any],
    *,
    catalogs: dict[str, dict[str, Any]],
    strict_gate: bool,
    tool_name: str,
    args: dict[str, Any] | None,
) -> tuple[bool, str]:
    if strict_gate and not catalogs:
        return False, (
            "Type-2 tool catalog missing while tools inject is active; "
            f"cannot validate tool {tool_name!r}"
        )

    catalog = _resolve_catalog_for_mcp_tool(tool_name, catalogs)
    if catalog is None:
        return _validate_unlisted_mcp_tool(
            payload,
            tool_name,
            catalogs=catalogs,
            strict_gate=strict_gate,
        )

    if catalog not in _GATED_CATALOGS:
        return True, ""

    return _validate_gated_catalog_tool(
        catalog,
        tool_name,
        args,
        catalogs=catalogs,
    )


def _validate_unlisted_mcp_tool(
    payload: dict[str, Any],
    tool_name: str,
    *,
    catalogs: dict[str, dict[str, Any]],
    strict_gate: bool,
) -> tuple[bool, str]:
    raw_name = _first_str(payload, "tool_name", "toolName", "tool", "name") or tool_name
    if strict_gate and (
        _raw_routes_through_cyt_mcp_server(raw_name)
        or _shares_cyt_mcp_proxy_prefix(tool_name, catalogs)
    ):
        return False, _deny_message_cyt_mcp(
            tool_name,
            {"name": tool_name, "input_schema": {}},
            "tool not in cyt_mcp Type-2 catalog",
        )
    return True, ""


def _validate_gated_catalog_tool(
    catalog: str,
    tool_name: str,
    args: dict[str, Any] | None,
    *,
    catalogs: dict[str, dict[str, Any]],
) -> tuple[bool, str]:
    tool = _find_tool_in_catalog(catalogs, catalog, tool_name)
    if tool is None:
        return _deny_missing_catalog_tool(catalog, tool_name)

    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        schema = {}
    payload_args = args if args is not None else {}
    ok, reason = validate_json_schema(payload_args, schema)
    if not ok:
        return _deny_catalog_schema_mismatch(catalog, tool_name, tool, reason)
    return True, ""


def _deny_missing_catalog_tool(catalog: str, tool_name: str) -> tuple[bool, str]:
    if catalog == "cyt_mcp":
        return False, _deny_message_cyt_mcp(
            tool_name,
            {"name": tool_name, "input_schema": {}},
            "tool not in cyt_mcp Type-2 catalog",
        )
    if catalog == "definitions":
        return False, _deny_message_definitions(
            tool_name,
            {"name": tool_name, "input_schema": {}},
            "tool not in definitions Type-2 catalog",
        )
    return False, f"tool {tool_name!r} not in gated catalog"


def _deny_catalog_schema_mismatch(
    catalog: str,
    tool_name: str,
    tool: dict[str, Any],
    reason: str,
) -> tuple[bool, str]:
    if catalog == "cyt_mcp":
        return False, _deny_message_cyt_mcp(tool_name, tool, reason)
    if catalog == "definitions":
        return False, _deny_message_definitions(tool_name, tool, reason)
    return False, reason


def validate_pre_tool_call(payload: dict[str, Any]) -> tuple[bool, str]:
    """Return (allow, reason). Type-2 catalog is the only authority for gating."""
    catalogs, inject_enabled, strict_gate = _load_gate_context(payload)

    if inject_enabled is False:
        return True, ""

    agent = infer_harness_agent(payload)
    tool_name, args = _extract_tool_call(payload)
    shell_command = _extract_shell_command(payload)

    if tool_name and tool_name in _SHELL_TOOL_NAMES and shell_command:
        shell_result = _validate_mcpc_shell_pre_tool_call(
            payload,
            catalogs=catalogs,
            strict_gate=strict_gate,
            shell_command=shell_command,
        )
        if shell_result is not None:
            return shell_result

    if not tool_name:
        return True, ""

    if is_cyt_mcp_get_tool_definitions_tool(tool_name, agent=agent):
        error = _validate_get_tool_definitions_args(args)
        if error:
            return False, error
        return True, ""

    return _validate_catalog_tool_pre_tool_call(
        payload,
        catalogs=catalogs,
        strict_gate=strict_gate,
        tool_name=tool_name,
        args=args,
    )


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

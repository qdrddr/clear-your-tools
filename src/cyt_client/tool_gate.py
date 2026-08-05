"""Rewrite tool_gate.py with Type-2 catalog validation."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt_client.agent import infer_harness_agent
from cyt_client.mcpc_shell import parse_mcpc_shell_command
from cyt_client.schema_validate import validate_json_schema
from cyt_client.session_pre_tool_exposure import PreToolDenyExposure
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


@dataclass(frozen=True)
class PreToolValidation:
    allowed: bool
    reason: str
    exposure: PreToolDenyExposure | None = None

    def __iter__(self) -> Iterator[bool | str]:
        yield self.allowed
        yield self.reason


def _allow(reason: str = "") -> PreToolValidation:
    return PreToolValidation(allowed=True, reason=reason)


def _deny(
    reason: str,
    *,
    exposure: PreToolDenyExposure | None = None,
) -> PreToolValidation:
    return PreToolValidation(allowed=False, reason=reason, exposure=exposure)


def _unknown_cyt_mcp_exposure(tool_name: str) -> PreToolDenyExposure:
    return PreToolDenyExposure(
        persist="get_tool_definitions",
        catalog="cyt_mcp",
        tool_name=tool_name,
    )


def _schema_mismatch_exposure(
    catalog: str,
    tool_name: str,
    tool: dict[str, Any],
    *,
    mcpc_session: str | None = None,
) -> PreToolDenyExposure:
    return PreToolDenyExposure(
        persist="catalog_tool",
        catalog=catalog,
        tool_name=tool_name,
        tool_record=dict(tool),
        mcpc_session=mcpc_session,
    )


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


def _minimized_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _is_unknown_tool_reason(reason: str) -> bool:
    return "not in" in reason and "Type-2 catalog" in reason


def _catalog_tool_names(
    catalogs: dict[str, dict[str, Any]],
    catalog: str,
    *,
    mcpc_session: str | None = None,
) -> list[str]:
    entry = catalogs.get(f"tool_catalog:{catalog}")
    if entry is None:
        return []
    tools = entry.get("tools")
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        if catalog == "mcpc" and mcpc_session:
            session = str(tool.get("mcpc_session") or "").strip()
            if session and session != mcpc_session:
                continue
        names.append(name)
    return sorted(set(names))


def _tool_definition_record(
    tool: dict[str, Any],
    *,
    catalog: str,
    mcpc_session: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": tool.get("name"),
        "input_schema": tool.get("input_schema") or {},
    }
    description = tool.get("description")
    if description is not None and str(description).strip():
        record["description"] = str(description).strip()
    if catalog == "mcpc":
        session = mcpc_session or tool.get("mcpc_session")
        if session:
            record["mcpc_session"] = session
    return record


def _get_tool_definitions_payload(tool_name: str) -> str:
    return _minimized_json({"tool_name": tool_name})


def _format_available_tools(names: list[str]) -> str:
    if not names:
        return "Available tools:\n(none)"
    return "Available tools:\n" + "\n".join(f"- {name}" for name in names)


def _deny_unknown_cyt_mcp(
    tool_name: str,
    reason: str,
    *,
    catalogs: dict[str, dict[str, Any]],
) -> str:
    available = _catalog_tool_names(catalogs, "cyt_mcp")
    return (
        f"Hallucinated cyt-mcp tool call for {tool_name!r}: {reason}\n\n"
        f"{_format_available_tools(available)}\n\n"
        "Use MCP tool `get-tool-definitions` with arguments:\n"
        f"{_get_tool_definitions_payload(tool_name)}"
    )


def _tool_deny_header(
    tool_name: str,
    *,
    requested_tool_name: str | None = None,
) -> str:
    requested = str(requested_tool_name or "").strip() or tool_name
    if requested != tool_name:
        return f"Tool {requested!r} (catalog name {tool_name!r})"
    return f"Tool {tool_name!r}"


def _deny_schema_cyt_mcp(
    tool_name: str,
    tool: dict[str, Any],
    reason: str,
    *,
    requested_tool_name: str | None = None,
) -> str:
    definition = _tool_definition_record(tool, catalog="cyt_mcp")
    if not definition.get("name"):
        definition["name"] = tool_name
    header = _tool_deny_header(tool_name, requested_tool_name=requested_tool_name)
    return (
        f"{header}: invalid cyt-mcp tool arguments: {reason}\n\n"
        f"Correct tool definition: {_minimized_json(definition)}"
    )


def _deny_message_cyt_mcp(
    tool_name: str,
    tool: dict[str, Any],
    schema_error: str,
    *,
    catalogs: dict[str, dict[str, Any]] | None = None,
    requested_tool_name: str | None = None,
) -> str:
    if _is_unknown_tool_reason(schema_error):
        header = _tool_deny_header(tool_name, requested_tool_name=requested_tool_name)
        body = _deny_unknown_cyt_mcp(
            tool_name,
            schema_error,
            catalogs=catalogs or {},
        )
        if requested_tool_name and requested_tool_name != tool_name:
            return body.replace(
                f"Hallucinated cyt-mcp tool call for {tool_name!r}:",
                f"{header}: hallucinated cyt-mcp tool call for {tool_name!r}:",
                1,
            )
        return body.replace(
            f"Hallucinated cyt-mcp tool call for {tool_name!r}:",
            f"{header}: hallucinated cyt-mcp tool call:",
            1,
        )
    return _deny_schema_cyt_mcp(
        tool_name,
        tool,
        schema_error,
        requested_tool_name=requested_tool_name,
    )


def _deny_unknown_mcpc(
    session: str,
    tool_name: str,
    reason: str,
    *,
    catalogs: dict[str, dict[str, Any]],
) -> str:
    available = _catalog_tool_names(catalogs, "mcpc", mcpc_session=session)
    return (
        f"Hallucinated mcpc Shell call for {session} tools-call {tool_name!r}: {reason}\n\n"
        f"{_format_available_tools(available)}\n\n"
        "Fix the Shell command to use a tool from the mcpc Type-2 catalog for this session."
    )


def _deny_schema_mcpc(
    session: str,
    tool_name: str,
    tool: dict[str, Any],
    reason: str,
) -> str:
    definition = _tool_definition_record(tool, catalog="mcpc", mcpc_session=session)
    if not definition.get("name"):
        definition["name"] = tool_name
    return (
        f"Invalid mcpc Shell arguments for {session} tools-call {tool_name!r}: {reason}\n\n"
        f"Correct tool definition: {_minimized_json(definition)}"
    )


def _deny_message_mcpc(
    session: str,
    tool_name: str,
    tool: dict[str, Any],
    schema_error: str,
    *,
    catalogs: dict[str, dict[str, Any]] | None = None,
) -> str:
    if _is_unknown_tool_reason(schema_error):
        return _deny_unknown_mcpc(
            session,
            tool_name,
            schema_error,
            catalogs=catalogs or {},
        )
    return _deny_schema_mcpc(session, tool_name, tool, schema_error)


def _deny_unknown_definitions(
    tool_name: str,
    reason: str,
    *,
    catalogs: dict[str, dict[str, Any]],
) -> str:
    available = _catalog_tool_names(catalogs, "definitions")
    return (
        f"Hallucinated definitions tool call for {tool_name!r}: {reason}\n\n"
        f"{_format_available_tools(available)}\n\n"
        "Use a tool name from the definitions Type-2 catalog."
    )


def _deny_schema_definitions(tool_name: str, tool: dict[str, Any], reason: str) -> str:
    definition = _tool_definition_record(tool, catalog="definitions")
    if not definition.get("name"):
        definition["name"] = tool_name
    return (
        f"Invalid definitions tool arguments for {tool_name!r}: {reason}\n\n"
        f"Correct tool definition: {_minimized_json(definition)}"
    )


def _deny_message_definitions(
    tool_name: str,
    tool: dict[str, Any],
    schema_error: str,
    *,
    catalogs: dict[str, dict[str, Any]] | None = None,
) -> str:
    if _is_unknown_tool_reason(schema_error):
        return _deny_unknown_definitions(
            tool_name,
            schema_error,
            catalogs=catalogs or {},
        )
    return _deny_schema_definitions(tool_name, tool, schema_error)


def _workspace_roots_from_payload(payload: dict[str, Any]) -> list[str]:
    roots: list[str] = []
    for layer in _payload_layers(payload):
        raw = layer.get("workspace_roots")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str) and item.strip():
                roots.append(item.strip())
    return roots


def _schema_fixup_hint(
    args: dict[str, Any],
    schema: dict[str, Any],
    payload: dict[str, Any] | None,
) -> str:
    """Actionable hint when raw args fail but a common rename/fill would succeed."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ""
    hints: list[str] = []

    if (
        "path" in args
        and "path" not in properties
        and "query" in properties
        and str(args.get("path") or "").strip()
    ):
        path = str(args["path"]).strip().rstrip("/") + "/"
        term = str(args.get("pattern") or args.get("query") or "").strip()
        merged = f"{path} {term}".strip() if term else path.rstrip("/")
        hints.append(f"Use query (not path). Example: {merged!r}")

    normalized = _normalize_property_aliases(dict(args), schema)
    if normalized != args:
        hints.append(f"Expected argument names: {sorted(normalized)}")

    required = schema.get("required")
    if isinstance(required, list) and payload is not None:
        roots = _workspace_roots_from_payload(payload)
        if roots:
            primary_root = roots[0]
            project_default = Path(primary_root).name or primary_root
            missing: list[str] = []
            for key, value in (("repo", primary_root), ("project", project_default)):
                if key in required and key not in args:
                    missing.append(f"{key}={value!r}")
            if missing:
                hints.append("Missing required: " + ", ".join(missing))

    return "\n".join(hints)


def _normalize_property_aliases(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Map common agent argument aliases onto catalog property names."""
    normalized = dict(args)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return normalized
    if (
        "pattern" in normalized
        and "pattern" not in properties
        and "query" in properties
        and "query" not in normalized
    ):
        normalized["query"] = normalized.pop("pattern")
    if (
        "query" in normalized
        and "query" not in properties
        and "pattern" in properties
        and "pattern" not in normalized
    ):
        normalized["pattern"] = normalized.pop("query")
    if (
        "query" in normalized
        and "query" not in properties
        and "search_query" in properties
        and "search_query" not in normalized
    ):
        normalized["search_query"] = normalized.pop("query")
    if (
        "limit" in normalized
        and "limit" not in properties
        and "top_k" in properties
        and "top_k" not in normalized
    ):
        normalized["top_k"] = normalized.pop("limit")
    return normalized


def _resolve_cyt_mcp_tool_name_for_catalog(
    tool_name: str,
    catalogs: dict[str, dict[str, Any]],
) -> str:
    """Map server-prefixed names (e.g. jcodemunch_search_symbols) to Type-2 catalog names."""
    if _find_tool_in_catalog(catalogs, "cyt_mcp", tool_name) is not None:
        return tool_name
    if "_" not in tool_name:
        return tool_name
    _prefix, _, suffix = tool_name.partition("_")
    if suffix and _find_tool_in_catalog(catalogs, "cyt_mcp", suffix) is not None:
        return suffix
    return tool_name


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
    resolved_name = _resolve_cyt_mcp_tool_name_for_catalog(tool_name, catalogs)
    for catalog in ("cyt_mcp", "definitions"):
        lookup_name = resolved_name if catalog == "cyt_mcp" else tool_name
        if _find_tool_in_catalog(catalogs, catalog, lookup_name) is not None:
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
    # Gate only when Type-2 tool_catalog partitions are present — not on inject flag alone.
    gate_active = bool(catalogs)
    return catalogs, inject_enabled, gate_active


def _validate_mcpc_shell_pre_tool_call(
    payload: dict[str, Any],
    *,
    catalogs: dict[str, dict[str, Any]],
    gate_active: bool,
    shell_command: str,
) -> PreToolValidation | None:
    mcpc_call = parse_mcpc_shell_command(shell_command)
    if mcpc_call is None:
        return None
    mcpc_catalog = catalogs.get("tool_catalog:mcpc")
    if not gate_active or mcpc_catalog is None:
        return _allow()
    tool = _find_tool_in_catalog(
        catalogs,
        "mcpc",
        mcpc_call.tool_name,
        mcpc_session=mcpc_call.session,
    )
    if tool is None:
        return _deny(
            _deny_message_mcpc(
                mcpc_call.session,
                mcpc_call.tool_name,
                {"name": mcpc_call.tool_name, "input_schema": {}},
                "tool not in mcpc Type-2 catalog for this session",
                catalogs=catalogs,
            ),
        )
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        schema = {}
    ok, reason = validate_json_schema(mcpc_call.args, schema)
    if not ok:
        return _deny(
            _deny_message_mcpc(
                mcpc_call.session,
                mcpc_call.tool_name,
                tool,
                reason,
            ),
            exposure=_schema_mismatch_exposure(
                "mcpc",
                mcpc_call.tool_name,
                tool,
                mcpc_session=mcpc_call.session,
            ),
        )
    return _allow()


def _validate_catalog_tool_pre_tool_call(
    payload: dict[str, Any],
    *,
    catalogs: dict[str, dict[str, Any]],
    gate_active: bool,
    tool_name: str,
    args: dict[str, Any] | None,
) -> PreToolValidation:
    if not gate_active:
        return _allow()

    catalog = _resolve_catalog_for_mcp_tool(tool_name, catalogs)
    if catalog is None:
        return _validate_unlisted_mcp_tool(
            payload,
            tool_name,
            catalogs=catalogs,
            gate_active=gate_active,
        )

    if catalog not in _GATED_CATALOGS:
        return _allow()

    catalog_tool_name = (
        _resolve_cyt_mcp_tool_name_for_catalog(tool_name, catalogs)
        if catalog == "cyt_mcp"
        else tool_name
    )
    return _validate_gated_catalog_tool(
        catalog,
        catalog_tool_name,
        args,
        catalogs=catalogs,
        payload=payload,
        requested_tool_name=tool_name,
    )


def _validate_unlisted_mcp_tool(
    payload: dict[str, Any],
    tool_name: str,
    *,
    catalogs: dict[str, dict[str, Any]],
    gate_active: bool,
) -> PreToolValidation:
    if not gate_active:
        return _allow()
    raw_name = _first_str(payload, "tool_name", "toolName", "tool", "name") or tool_name
    cyt_mcp_catalog = catalogs.get("tool_catalog:cyt_mcp")
    if cyt_mcp_catalog is None:
        return _allow()
    if _raw_routes_through_cyt_mcp_server(raw_name) or _shares_cyt_mcp_proxy_prefix(
        tool_name,
        catalogs,
    ):
        return _deny(
            _deny_message_cyt_mcp(
                tool_name,
                {"name": tool_name, "input_schema": {}},
                "tool not in cyt_mcp Type-2 catalog",
                catalogs=catalogs,
            ),
            exposure=_unknown_cyt_mcp_exposure(tool_name),
        )
    return _allow()


def _validate_gated_catalog_tool(
    catalog: str,
    tool_name: str,
    args: dict[str, Any] | None,
    *,
    catalogs: dict[str, dict[str, Any]],
    payload: dict[str, Any] | None = None,
    requested_tool_name: str | None = None,
) -> PreToolValidation:
    tool = _find_tool_in_catalog(catalogs, catalog, tool_name)
    if tool is None:
        return _deny_missing_catalog_tool(catalog, tool_name, catalogs=catalogs)

    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        schema = {}
    raw_args = args if args is not None else {}
    ok, reason = validate_json_schema(raw_args, schema)
    if not ok:
        fixup = _schema_fixup_hint(raw_args, schema, payload)
        if fixup:
            reason = f"{reason}\n\n{fixup}"
        return _deny_catalog_schema_mismatch(
            catalog,
            tool_name,
            tool,
            reason,
            requested_tool_name=requested_tool_name,
        )
    return _allow()


def _deny_missing_catalog_tool(
    catalog: str,
    tool_name: str,
    *,
    catalogs: dict[str, dict[str, Any]],
) -> PreToolValidation:
    if catalog == "cyt_mcp":
        return _deny(
            _deny_message_cyt_mcp(
                tool_name,
                {"name": tool_name, "input_schema": {}},
                "tool not in cyt_mcp Type-2 catalog",
                catalogs=catalogs,
            ),
            exposure=_unknown_cyt_mcp_exposure(tool_name),
        )
    if catalog == "definitions":
        return _deny(
            _deny_message_definitions(
                tool_name,
                {"name": tool_name, "input_schema": {}},
                "tool not in definitions Type-2 catalog",
                catalogs=catalogs,
            ),
        )
    return _deny(f"tool {tool_name!r} not in gated catalog")


def _deny_catalog_schema_mismatch(
    catalog: str,
    tool_name: str,
    tool: dict[str, Any],
    reason: str,
    *,
    requested_tool_name: str | None = None,
) -> PreToolValidation:
    exposure = _schema_mismatch_exposure(catalog, tool_name, tool)
    if catalog == "cyt_mcp":
        return _deny(
            _deny_message_cyt_mcp(
                tool_name,
                tool,
                reason,
                requested_tool_name=requested_tool_name,
            ),
            exposure=exposure,
        )
    if catalog == "definitions":
        return _deny(
            _deny_message_definitions(tool_name, tool, reason),
            exposure=exposure,
        )
    return _deny(reason, exposure=exposure)


def validate_pre_tool_call(payload: dict[str, Any]) -> PreToolValidation:
    """Return validation outcome. Type-2 catalog is the only authority for gating."""
    catalogs, inject_enabled, gate_active = _load_gate_context(payload)

    if inject_enabled is False:
        return _allow()

    agent = infer_harness_agent(payload)
    tool_name, args = _extract_tool_call(payload)
    shell_command = _extract_shell_command(payload)

    if tool_name and tool_name in _SHELL_TOOL_NAMES and shell_command:
        shell_result = _validate_mcpc_shell_pre_tool_call(
            payload,
            catalogs=catalogs,
            gate_active=gate_active,
            shell_command=shell_command,
        )
        if shell_result is not None:
            return shell_result

    if not tool_name:
        return _allow()

    if is_cyt_mcp_get_tool_definitions_tool(tool_name, agent=agent):
        error = _validate_get_tool_definitions_args(args)
        if error:
            return _deny(error)
        return _allow()

    if not gate_active:
        return _allow()

    return _validate_catalog_tool_pre_tool_call(
        payload,
        catalogs=catalogs,
        gate_active=gate_active,
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

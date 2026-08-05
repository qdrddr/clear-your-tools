"""Persist Type-1 tool entries after PreToolUse deny (stdlib only)."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cyt_client.agent import infer_harness_agent
from cyt_client.sessions import (
    append_session_log,
    read_latest_tool_catalogs,
    read_session_log_file,
    session_log_path,
)

_TOOL_DEF_HASH_PREFIX = b"v1-tool-def\x00"
_GET_TOOL_DEFINITIONS_TOOL = "cyt-mcp_get-tool-definitions"
_GET_TOOL_DEFINITIONS_DESCRIPTION = (
    "Returns the full MCP tool definition for a cyt-mcp backend tool by name. "
    "Use when hook-injected stubs lack properties or metadata you need. "
    "The tool_name argument must be one of the backend tools exposed by this server."
)
_PRE_TOOL_DENY_SOURCE = "cyt-client_pre-tool-deny"


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


@dataclass(frozen=True)
class PreToolDenyExposure:
    """What to append to the session log after a PreToolUse deny."""

    persist: Literal["catalog_tool", "get_tool_definitions"]
    catalog: str
    tool_name: str
    tool_record: dict[str, Any] | None = None
    mcpc_session: str | None = None


def _tool_definition_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(_TOOL_DEF_HASH_PREFIX + canonical.encode("utf-8")).hexdigest()


def _cyt_mcp_or_definitions_hash(
    name: str,
    input_schema: dict[str, Any],
    *,
    description: str | None = None,
) -> str:
    definition: dict[str, Any] = {
        "name": name,
        "input_schema": input_schema if isinstance(input_schema, dict) else {},
    }
    if description is not None:
        definition["description"] = description
    return _tool_definition_hash(definition)


def _mcpc_hash(
    tool_record: dict[str, Any],
    *,
    mcpc_session: str | None,
) -> str:
    schema = tool_record.get("input_schema") or {}
    if not isinstance(schema, dict):
        schema = {}
    name = str(tool_record.get("name") or "").strip()
    if not name:
        session = str(mcpc_session or tool_record.get("mcpc_session") or "").strip()
        tool_name = str(tool_record.get("tool_name") or "").strip()
        name = f"{session}/{tool_name}" if session else tool_name
    definition: dict[str, Any] = {
        "name": name,
        "id": name,
        "inputSchema": schema,
    }
    description = tool_record.get("description")
    if description is not None and str(description).strip():
        definition["description"] = str(description).strip()
    return _tool_definition_hash(definition)


def _tool_item_key(
    catalog: str,
    tool_name: str,
    *,
    mcpc_session: str | None = None,
) -> str:
    if catalog == "mcpc":
        session = str(mcpc_session or "").strip()
        return f"tool:mcpc:{session}:{tool_name}"
    if catalog == "cyt_mcp":
        return f"tool:cyt_mcp:{tool_name}"
    if catalog == "definitions":
        return f"tool:definitions:{tool_name}"
    return f"tool:executor:{tool_name}"


def build_type1_tool_entry_from_catalog_record(
    tool_record: dict[str, Any],
    *,
    catalog: str,
    mcpc_session: str | None = None,
    full: bool = True,
) -> dict[str, Any]:
    name = str(tool_record.get("name") or "").strip()
    schema = tool_record.get("input_schema") or {}
    if not isinstance(schema, dict):
        schema = {}
    description = tool_record.get("description")
    description_text = str(description).strip() if description is not None else None

    if catalog == "mcpc":
        session = str(mcpc_session or tool_record.get("mcpc_session") or "").strip()
        content_hash = _mcpc_hash(tool_record, mcpc_session=session or None)
        entry: dict[str, Any] = {
            "kind": "tool",
            "key": _tool_item_key("mcpc", name, mcpc_session=session or None),
            "hash": content_hash,
            "full": full,
            "catalog": "mcpc",
            "name": name,
            "mcpc_session": session,
            "input_schema": deepcopy(schema),
            "source": _PRE_TOOL_DENY_SOURCE,
        }
        if description_text:
            entry["description"] = description_text
        entry["title"] = name
        return entry

    content_hash = _cyt_mcp_or_definitions_hash(
        name,
        schema,
        description=description_text,
    )
    entry = {
        "kind": "tool",
        "key": _tool_item_key(catalog, name),
        "hash": content_hash,
        "full": full,
        "catalog": catalog,
        "name": name,
        "input_schema": deepcopy(schema),
        "source": _PRE_TOOL_DENY_SOURCE,
    }
    if catalog == "cyt_mcp":
        entry["source"] = _PRE_TOOL_DENY_SOURCE
    if description_text:
        entry["description"] = description_text
    return entry


def _minimal_get_tool_definitions_record() -> dict[str, Any]:
    return {
        "name": _GET_TOOL_DEFINITIONS_TOOL,
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Backend cyt-mcp tool name to look up.",
                },
            },
            "required": ["tool_name"],
            "additionalProperties": False,
        },
        "description": _GET_TOOL_DEFINITIONS_DESCRIPTION,
    }


def build_get_tool_definitions_type1_entry(
    catalogs: dict[str, dict[str, Any]] | None = None,
    *,
    full: bool = True,
) -> dict[str, Any]:
    tool_record = _minimal_get_tool_definitions_record()
    if catalogs:
        from_catalog = _find_tool_in_catalog(
            catalogs,
            "cyt_mcp",
            _GET_TOOL_DEFINITIONS_TOOL,
        )
        if from_catalog is not None:
            tool_record = dict(from_catalog)
            if from_catalog.get("description") is not None:
                tool_record["description"] = str(from_catalog["description"])
    return build_type1_tool_entry_from_catalog_record(
        tool_record,
        catalog="cyt_mcp",
        full=full,
    )


def _session_has_full_tool(path: Path, key: str, content_hash: str) -> bool:
    _agent, entries = read_session_log_file(path)
    for entry in reversed(entries):
        if str(entry.get("kind") or "") != "tool":
            continue
        if str(entry.get("key") or "") != key:
            continue
        if entry.get("full") and str(entry.get("hash") or "") == content_hash:
            return True
    return False


def persist_pre_tool_deny_exposure(
    payload: dict[str, Any],
    exposure: PreToolDenyExposure | None,
) -> bool:
    if exposure is None:
        return False
    path = session_log_path(payload)
    if path is None:
        return False

    if exposure.persist == "get_tool_definitions":
        catalogs = read_latest_tool_catalogs(path)
        entry = build_get_tool_definitions_type1_entry(catalogs, full=True)
    else:
        if exposure.tool_record is None:
            return False
        entry = build_type1_tool_entry_from_catalog_record(
            exposure.tool_record,
            catalog=exposure.catalog,
            mcpc_session=exposure.mcpc_session,
            full=True,
        )

    if _session_has_full_tool(path, str(entry["key"]), str(entry["hash"])):
        return False

    agent = infer_harness_agent(payload)
    append_session_log(path, [entry], agent=agent)
    return True

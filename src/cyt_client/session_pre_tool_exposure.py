"""Persist Type-1 tool entries after PreToolUse deny."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from cyt.injection.pre_exposure_context import PreExposureContext
from cyt.injection.session_log import resolve_injection_mode
from cyt.injection.session_log_build import (
    CatalogKind,
    format_tool_fragment,
    tool_content_hash,
    tool_item_key,
    tool_item_legacy_keys,
)
from cyt_client.agent import infer_harness_agent
from cyt_client.catalog_hash import catalog_tool_record_content_hash
from cyt_client.sessions import (
    append_session_log,
    entries_after_latest_compaction,
    read_latest_tool_catalogs,
    read_session_log_file,
    session_log_path,
)

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


def _catalog_lookup_name(
    catalog: str,
    tool_name: str,
    catalogs: dict[str, dict[str, Any]],
) -> str:
    return tool_name


def _tool_record_from_session_catalog(
    exposure: PreToolDenyExposure,
    catalogs: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    lookup_name = _catalog_lookup_name(exposure.catalog, exposure.tool_name, catalogs)
    from_catalog = _find_tool_in_catalog(
        catalogs,
        exposure.catalog,
        lookup_name,
        mcpc_session=exposure.mcpc_session,
    )
    if from_catalog is not None:
        return dict(from_catalog)
    if exposure.tool_record is not None:
        return dict(exposure.tool_record)
    return None


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

    explicit_hash = tool_record.get("hash")
    content_hash = (
        str(explicit_hash).strip()
        if isinstance(explicit_hash, str) and explicit_hash.strip()
        else catalog_tool_record_content_hash(
            catalog,
            {
                "name": name,
                "input_schema": schema,
                **({"description": description_text} if description_text else {}),
                **(
                    {
                        "mcpc_session": str(
                            mcpc_session or tool_record.get("mcpc_session") or "",
                        ).strip(),
                    }
                    if catalog == "mcpc" and (mcpc_session or tool_record.get("mcpc_session"))
                    else {}
                ),
            },
        )
    )

    if catalog == "mcpc":
        session = str(mcpc_session or tool_record.get("mcpc_session") or "").strip()
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


def _deny_definition_record(
    tool_record: dict[str, Any],
    *,
    catalog: str,
    mcpc_session: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": tool_record.get("name"),
        "input_schema": tool_record.get("input_schema") or {},
    }
    description = tool_record.get("description")
    if description is not None and str(description).strip():
        record["description"] = str(description).strip()
    if catalog == "mcpc":
        session = mcpc_session or tool_record.get("mcpc_session")
        if session:
            record["mcpc_session"] = session
    return record


def _minimized_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _master_tool_for_pre_exposure(
    tool_record: dict[str, Any],
    *,
    catalog: str,
    mcpc_session: str | None = None,
) -> dict[str, Any]:
    name = str(tool_record.get("name") or "").strip()
    master: dict[str, Any] = {
        "name": name,
        "tool_name": name,
        "input_schema": tool_record.get("input_schema") or {},
        "cyt_catalog_source": catalog,
    }
    description = tool_record.get("description")
    if description is not None and str(description).strip():
        master["description"] = str(description).strip()
    if catalog == "mcpc":
        session = str(mcpc_session or tool_record.get("mcpc_session") or "").strip()
        if session:
            master["mcpc_session"] = session
    return master


def is_tool_definition_pre_exposed_for_deny(
    payload: dict[str, Any],
    *,
    catalog: str,
    tool_record: dict[str, Any],
    mcpc_session: str | None = None,
) -> bool:
    """Return True when the inline deny definition should be omitted for this context window."""
    ctx = PreExposureContext.for_hook_payload(payload)
    catalog_kind = cast(CatalogKind, catalog)
    master = _master_tool_for_pre_exposure(
        tool_record,
        catalog=catalog,
        mcpc_session=mcpc_session,
    )
    key = tool_item_key(master, catalog=catalog_kind)
    key_aliases = tool_item_legacy_keys(master, catalog=catalog_kind)
    current_hash = tool_content_hash(master, catalog=catalog_kind)
    skinny_fragment = format_tool_fragment(
        master,
        catalog=catalog_kind,
        full=False,
        include_tool_description=True,
    )
    full_fragment = format_tool_fragment(
        master,
        catalog=catalog_kind,
        full=True,
        include_tool_description=True,
    )
    mode = resolve_injection_mode(
        key=key,
        current_hash=current_hash,
        index=ctx.index,
        session_text=ctx.payload_text,
        formatted_skinny=skinny_fragment,
        formatted_full=full_fragment,
        key_aliases=key_aliases,
    )
    if mode == "skip":
        return True

    definition = _deny_definition_record(
        tool_record,
        catalog=catalog,
        mcpc_session=mcpc_session,
    )
    name = str(tool_record.get("name") or "").strip()
    if not definition.get("name"):
        definition["name"] = name
    json_def = _minimized_json(definition)
    deny_line = f"Correct tool definition: {json_def}"
    for text in (ctx.payload_text, ctx.combined_text):
        if json_def in text or deny_line in text:
            return True
    return False


def _session_has_full_tool(path: Path, key: str, content_hash: str) -> bool:
    _agent, entries = read_session_log_file(path)
    entries = entries_after_latest_compaction(entries)
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

    catalogs = read_latest_tool_catalogs(path)

    if exposure.persist == "get_tool_definitions":
        entry = build_get_tool_definitions_type1_entry(catalogs, full=True)
    else:
        tool_record = _tool_record_from_session_catalog(exposure, catalogs)
        if tool_record is None:
            return False
        if is_tool_definition_pre_exposed_for_deny(
            payload,
            catalog=exposure.catalog,
            tool_record=tool_record,
            mcpc_session=exposure.mcpc_session,
        ):
            return False
        entry = build_type1_tool_entry_from_catalog_record(
            tool_record,
            catalog=exposure.catalog,
            mcpc_session=exposure.mcpc_session,
            full=True,
        )

    if _session_has_full_tool(path, str(entry["key"]), str(entry["hash"])):
        return False

    agent = infer_harness_agent(payload)
    append_session_log(path, [entry], agent=agent)
    return True

"""Per-tool content hashes for Type-1/Type-2 session log records (stdlib only)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_TOOL_DEF_HASH_PREFIX = b"v1-tool-def\x00"


def tool_definition_content_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(_TOOL_DEF_HASH_PREFIX + canonical.encode("utf-8")).hexdigest()


def _cyt_mcp_or_executor_definition(record: dict[str, Any]) -> dict[str, Any]:
    schema = record.get("input_schema") or {}
    if not isinstance(schema, dict):
        schema = {}
    definition: dict[str, Any] = {
        "name": str(record.get("name") or "").strip(),
        "input_schema": schema,
    }
    description = record.get("description")
    if description is not None and str(description).strip():
        definition["description"] = str(description).strip()
    return definition


def _mcpc_definition(record: dict[str, Any]) -> dict[str, Any]:
    schema = record.get("input_schema") or {}
    if not isinstance(schema, dict):
        schema = {}
    name = str(record.get("name") or "").strip()
    if not name:
        session = str(record.get("mcpc_session") or "").strip()
        tool_name = str(record.get("tool_name") or "").strip()
        name = f"{session}/{tool_name}" if session else tool_name
    definition: dict[str, Any] = {
        "name": name,
        "id": name,
        "inputSchema": schema,
    }
    description = record.get("description")
    if description is not None and str(description).strip():
        definition["description"] = str(description).strip()
    return definition


def catalog_tool_record_content_hash(catalog: str, record: dict[str, Any]) -> str:
    """Hash a Type-2 catalog tool record; matches hook Type-1 ``tool_content_hash`` for full schemas."""
    if catalog == "mcpc":
        return tool_definition_content_hash(_mcpc_definition(record))
    return tool_definition_content_hash(_cyt_mcp_or_executor_definition(record))


def catalog_tool_record_core(record: dict[str, Any]) -> dict[str, Any]:
    """Strip per-tool hash before catalog bundle hashing."""
    return {key: value for key, value in record.items() if key != "hash"}

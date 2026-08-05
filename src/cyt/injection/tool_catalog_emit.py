"""Emit Type-2 tool_catalog session log entries from the unpruned master catalog."""

from __future__ import annotations

from typing import Any

from cyt.injection.session_log_build import (
    build_session_state_entry,
    build_tool_catalog_log_entry,
    build_tool_catalog_stub_entry,
    catalog_bundle_content_hash,
    catalog_source_order,
    partition_catalog_by_source,
)


def tool_catalog_hashes_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("tool_catalog_hashes")
    if not isinstance(raw, dict):
        return {}
    hashes: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip():
            hashes[key.strip()] = value.strip()
    return hashes


def emit_tool_catalog_session_log(
    catalog: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    tools_inject_enabled: bool,
) -> list[dict[str, Any]]:
    """Build Type-2 catalog entries + session_state for cytSessionLog."""
    entries: list[dict[str, Any]] = [
        build_session_state_entry(tools_inject_enabled=tools_inject_enabled),
    ]
    client_hashes = tool_catalog_hashes_from_payload(payload)
    partitions = partition_catalog_by_source(catalog)
    for catalog_kind in catalog_source_order():
        tools = partitions.get(catalog_kind) or []
        if not tools:
            continue
        content_hash = catalog_bundle_content_hash(catalog_kind, tools)
        key = f"tool_catalog:{catalog_kind}"
        if client_hashes.get(key) == content_hash:
            entries.append(build_tool_catalog_stub_entry(catalog_kind, content_hash))
        else:
            entries.append(build_tool_catalog_log_entry(catalog_kind, tools))
    return entries


def merge_session_log_entries(
    existing: list[dict[str, Any]] | None,
    new_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not new_entries:
        return list(existing or [])
    if not existing:
        return list(new_entries)
    return list(existing) + list(new_entries)


def append_tool_catalog_to_details(
    details: dict[str, Any],
    catalog: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    tools_inject_enabled: bool,
) -> None:
    catalog_entries = emit_tool_catalog_session_log(
        catalog,
        payload=payload,
        tools_inject_enabled=tools_inject_enabled,
    )
    details["tools_inject_enabled"] = tools_inject_enabled
    existing = details.get("session_log")
    if isinstance(existing, list):
        details["session_log"] = merge_session_log_entries(existing, catalog_entries)
    else:
        details["session_log"] = catalog_entries

"""Build verify-only session log entries (Type-2 catalog + Type-1 full tools)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.injection.session_log_build import (
    build_session_state_entry,
    build_tool_catalog_log_entry,
    build_tool_log_entry,
    catalog_bundle_content_hash,
    partition_catalog_by_source,
)


def existing_tool_keys_and_hashes(path: Path) -> set[tuple[str, str]]:
    """Return (key, hash) pairs already present in a session jsonl file."""
    if not path.is_file():
        return set()
    from cyt_client.sessions import read_session_log_file

    _agent, entries = read_session_log_file(path)
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if entry.get("kind") != "tool":
            continue
        key = str(entry.get("key") or "").strip()
        content_hash = str(entry.get("hash") or "").strip()
        if key and content_hash:
            seen.add((key, content_hash))
    return seen


def filter_new_tool_entries(
    path: Path | None,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Type-1 bulk dedup: skip tools whose (key, hash) already exist on disk."""
    if not entries:
        return []
    existing = existing_tool_keys_and_hashes(path) if path is not None else set()
    fresh: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("kind") != "tool":
            fresh.append(entry)
            continue
        key = str(entry.get("key") or "").strip()
        content_hash = str(entry.get("hash") or "").strip()
        if key and content_hash and (key, content_hash) in existing:
            continue
        fresh.append(entry)
        if key and content_hash:
            existing.add((key, content_hash))
    return fresh


def build_verify_session_log_entries(
    tools: list[dict[str, Any]],
    *,
    tools_inject_enabled: bool = False,
    hallucination_gate_enabled: bool = True,
    inject_via: str | None = None,
    existing_catalog_hashes: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build session_state + Type-2 catalogs + Type-1 full tool entries for verify-only."""
    entries: list[dict[str, Any]] = [
        build_session_state_entry(
            tools_inject_enabled=tools_inject_enabled,
            hallucination_gate_enabled=hallucination_gate_enabled,
            inject_via=inject_via,
        ),
    ]
    partitions = partition_catalog_by_source(tools)
    prior_hashes = existing_catalog_hashes or {}
    for catalog, catalog_tools in partitions.items():
        if not catalog_tools:
            continue
        content_hash = catalog_bundle_content_hash(catalog, catalog_tools)
        catalog_key = f"tool_catalog:{catalog}"
        if prior_hashes.get(catalog_key) == content_hash:
            continue
        entries.append(build_tool_catalog_log_entry(catalog, catalog_tools))
        for tool in catalog_tools:
            entries.append(
                build_tool_log_entry(
                    tool,
                    catalog=catalog,
                    full=True,
                    catalog_tools=catalog_tools,
                ),
            )
    return entries


def append_verify_session_log(
    path: Path,
    tools: list[dict[str, Any]],
    *,
    agent: str,
    tools_inject_enabled: bool = False,
    hallucination_gate_enabled: bool = True,
    inject_via: str | None = None,
) -> list[dict[str, Any]]:
    """Build, dedup, and append verify-only entries to session jsonl."""
    from cyt_client.sessions import append_session_log, read_tool_catalog_hashes

    prior_hashes = read_tool_catalog_hashes(path)
    built = build_verify_session_log_entries(
        tools,
        tools_inject_enabled=tools_inject_enabled,
        hallucination_gate_enabled=hallucination_gate_enabled,
        inject_via=inject_via,
        existing_catalog_hashes=prior_hashes,
    )
    tool_entries = [entry for entry in built if entry.get("kind") == "tool"]
    non_tool = [entry for entry in built if entry.get("kind") != "tool"]
    deduped_tools = filter_new_tool_entries(path, tool_entries)
    to_append = non_tool + deduped_tools
    if to_append:
        append_session_log(path, to_append, agent=agent)
    return to_append

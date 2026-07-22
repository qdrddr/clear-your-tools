"""Apply session-log gates to hook injection candidates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from cyt.config import uses_mcpc_tool_catalog
from cyt.injection.session_log import SessionLogIndex, resolve_injection_mode
from cyt.injection.session_log_build import (
    CatalogKind,
    build_resource_log_entry,
    build_skill_log_entry,
    build_tool_log_entry,
    format_resource_fragment,
    format_skill_fragment,
    format_tool_fragment,
    resource_content_hash,
    resource_item_key,
    skill_content_hash,
    skill_item_key,
    tool_content_hash,
    tool_item_key,
)
from cyt.resources.inject import MatchedResource
from cyt.skills.inject import _resolve_skill_command
from cyt.skills.search import MatchedSkill


def _tool_catalog_kind(config: dict[str, Any]) -> CatalogKind:
    return "mcpc" if uses_mcpc_tool_catalog(config) else "executor"


def _full_tool_from_catalog(
    tool: dict[str, Any],
    catalog_tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if not catalog_tools:
        return tool
    name = str(tool.get("tool_name") or tool.get("name") or "").strip()
    session = str(tool.get("mcpc_session") or "").strip()
    for original in catalog_tools:
        orig_name = str(original.get("tool_name") or original.get("name") or "").strip()
        if orig_name != name:
            continue
        if session and str(original.get("mcpc_session") or "").strip() != session:
            continue
        return deepcopy(original)
    return tool


def gate_tools_for_session(
    tools: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    session_text: str,
    index: SessionLogIndex,
    catalog_tools: list[dict[str, Any]] | None = None,
    include_tool_description: bool = True,
    server_context: dict[tuple[str, str], dict[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, bool]]:
    """Return gated tools, session log entries, and full flags by tool name key."""
    catalog = _tool_catalog_kind(config)
    kept: list[dict[str, Any]] = []
    log_entries: list[dict[str, Any]] = []
    full_flags: dict[str, bool] = {}

    for tool in tools:
        working = deepcopy(tool)
        key = tool_item_key(working, catalog=catalog)
        full_source = _full_tool_from_catalog(working, catalog_tools)
        current_hash = tool_content_hash(full_source, catalog=catalog, catalog_tools=catalog_tools)
        skinny_fragment = format_tool_fragment(
            working,
            catalog=catalog,
            full=False,
            include_tool_description=include_tool_description,
        )
        full_fragment = format_tool_fragment(
            full_source,
            catalog=catalog,
            full=True,
            include_tool_description=include_tool_description,
        )
        mode = resolve_injection_mode(
            key=key,
            current_hash=current_hash,
            index=index,
            session_text=session_text,
            formatted_skinny=skinny_fragment,
            formatted_full=full_fragment,
        )
        if mode == "skip":
            continue
        inject_tool = full_source if mode == "full" else working
        kept.append(inject_tool)
        full_flags[key] = mode == "full"
        server = None
        if catalog == "mcpc" and server_context is not None:
            session = str(inject_tool.get("mcpc_session") or "").strip()
            name = str(inject_tool.get("tool_name") or inject_tool.get("name") or "").strip()
            server = server_context.get((session, name))
        log_entries.append(
            build_tool_log_entry(
                inject_tool,
                catalog=catalog,
                full=mode == "full",
                include_tool_description=include_tool_description,
                server=server,
                catalog_tools=catalog_tools,
            ),
        )
    return kept, log_entries, full_flags


def gate_skills_for_session(
    matches: list[MatchedSkill],
    *,
    session_text: str,
    index: SessionLogIndex,
) -> tuple[list[MatchedSkill], list[dict[str, Any]], dict[str, bool]]:
    kept: list[MatchedSkill] = []
    log_entries: list[dict[str, Any]] = []
    full_flags: dict[str, bool] = {}

    for match in matches:
        command = _resolve_skill_command(match)
        key = skill_item_key(match, command=command)
        current_hash = skill_content_hash(match)
        skinny_fragment = format_skill_fragment(match, full=False)
        full_fragment = format_skill_fragment(match, full=True)
        mode = resolve_injection_mode(
            key=key,
            current_hash=current_hash,
            index=index,
            session_text=session_text,
            formatted_skinny=skinny_fragment,
            formatted_full=full_fragment,
        )
        if mode == "skip":
            continue
        kept.append(match)
        full_flags[key] = mode == "full"
        log_entries.append(build_skill_log_entry(match, full=mode == "full"))
    return kept, log_entries, full_flags


def gate_resources_for_session(
    matches: list[MatchedResource],
    *,
    session_text: str,
    index: SessionLogIndex,
) -> tuple[list[MatchedResource], list[dict[str, Any]], dict[str, bool]]:
    kept: list[MatchedResource] = []
    log_entries: list[dict[str, Any]] = []
    full_flags: dict[str, bool] = {}

    for match in matches:
        key = resource_item_key(match)
        current_hash = resource_content_hash(match)
        skinny_fragment = format_resource_fragment(match, full=False)
        full_fragment = format_resource_fragment(match, full=True)
        mode = resolve_injection_mode(
            key=key,
            current_hash=current_hash,
            index=index,
            session_text=session_text,
            formatted_skinny=skinny_fragment,
            formatted_full=full_fragment,
        )
        if mode == "skip":
            continue
        kept.append(match)
        full_flags[key] = mode == "full"
        log_entries.append(build_resource_log_entry(match, full=mode == "full"))
    return kept, log_entries, full_flags

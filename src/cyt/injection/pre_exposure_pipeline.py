"""Shared gate+filter helpers for pre-exposure across hook and proxy paths."""

from __future__ import annotations

from typing import Any

from cyt.config import uses_mcpc_tool_catalog
from cyt.injection.mcpc_pre_exposed import filter_pre_exposed_mcpc_tools
from cyt.injection.pre_exposed import (
    filter_pre_exposed_native_tools,
    filter_pre_exposed_resources,
    filter_pre_exposed_skills,
    filter_pre_exposed_tools,
)
from cyt.injection.pre_exposure_context import PreExposureContext
from cyt.injection.session_gate import (
    gate_resources_for_session,
    gate_skills_for_session,
    gate_tools_for_session,
)
from cyt.injection.session_log_build import CatalogKind, tool_item_key
from cyt.resources.inject import MatchedResource
from cyt.skills.search import MatchedSkill
from cyt.tools.mcpc_prune import split_mcpc_prune_result


def _catalog_kind_for_source_id(source_id: str) -> CatalogKind:
    if source_id == "mcpc":
        return "mcpc"
    if source_id == "cyt_mcp":
        return "cyt_mcp"
    if source_id == "cloudflare":
        return "cloudflare"
    if source_id == "definitions":
        return "definitions"
    return "executor"


def gate_and_filter_tools(
    tools: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    ctx: PreExposureContext,
    catalog_tools: list[dict[str, Any]] | None = None,
    source_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str] | None]:
    """Session gate (b) plus verbatim filter on combined corpus."""
    index = ctx.index
    session_text = ctx.payload_text
    combined_text = ctx.combined_text
    surviving_instruction_sessions: set[str] | None = None
    working_tools = tools

    use_mcpc = source_id == "mcpc" or (source_id is None and uses_mcpc_tool_catalog(config))
    if use_mcpc:
        working_tools, surviving_instruction_sessions = split_mcpc_prune_result(tools)
        session_gated, log_entries, _full_flags = gate_tools_for_session(
            working_tools,
            config=config,
            session_text=session_text,
            index=index,
            catalog_tools=catalog_tools,
        )
        payload_gated = filter_pre_exposed_tools(
            session_gated,
            ctx.payload_text,
            include_tool_description=True,
        )
        gated = filter_pre_exposed_mcpc_tools(payload_gated, combined_text)
        catalog_kind: CatalogKind = "mcpc"
    else:
        session_gated, log_entries, _full_flags = gate_tools_for_session(
            working_tools,
            config=config,
            session_text=session_text,
            index=index,
            catalog_tools=catalog_tools,
        )
        payload_gated = filter_pre_exposed_tools(
            session_gated,
            ctx.payload_text,
            include_tool_description=True,
        )
        gated = filter_pre_exposed_tools(payload_gated, combined_text)
        if source_id:
            catalog_kind = _catalog_kind_for_source_id(source_id)
        else:
            catalog_kind = "mcpc" if uses_mcpc_tool_catalog(config) else "executor"

    gated_keys = {tool_item_key(tool, catalog=catalog_kind) for tool in gated}
    filtered_logs = [entry for entry in log_entries if str(entry.get("key") or "") in gated_keys]
    return gated, filtered_logs, surviving_instruction_sessions


def gate_and_filter_native_tools(
    tools: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    ctx: PreExposureContext,
    catalog_tools: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    session_gated, log_entries, _full_flags = gate_tools_for_session(
        tools,
        config=config,
        session_text=ctx.payload_text,
        index=ctx.index,
        catalog_tools=catalog_tools,
    )
    gated = filter_pre_exposed_native_tools(session_gated, ctx, config=config)
    catalog_kind: CatalogKind = "mcpc" if uses_mcpc_tool_catalog(config) else "executor"
    gated_keys = {tool_item_key(tool, catalog=catalog_kind) for tool in gated}
    filtered_logs = [entry for entry in log_entries if str(entry.get("key") or "") in gated_keys]
    return gated, filtered_logs


def gate_and_filter_skills(
    matches: list[MatchedSkill],
    *,
    config: dict[str, Any],
    ctx: PreExposureContext,
) -> tuple[list[MatchedSkill], list[dict[str, Any]]]:
    session_gated, log_entries, _full_flags = gate_skills_for_session(
        matches,
        session_text=ctx.payload_text,
        index=ctx.index,
    )
    payload_gated = filter_pre_exposed_skills(session_gated, ctx.payload_text)
    gated = filter_pre_exposed_skills(payload_gated, ctx.combined_text)
    from cyt.injection.session_log_build import skill_item_key

    gated_keys = {skill_item_key(match) for match in gated}
    filtered_logs = [entry for entry in log_entries if str(entry.get("key") or "") in gated_keys]
    return gated, filtered_logs


def gate_and_filter_resources(
    matches: list[MatchedResource],
    *,
    config: dict[str, Any],
    ctx: PreExposureContext,
) -> tuple[list[MatchedResource], list[dict[str, Any]]]:
    session_gated, log_entries, _full_flags = gate_resources_for_session(
        matches,
        session_text=ctx.payload_text,
        index=ctx.index,
    )
    payload_gated = filter_pre_exposed_resources(session_gated, ctx.payload_text)
    gated = filter_pre_exposed_resources(payload_gated, ctx.combined_text)
    from cyt.injection.session_log_build import resource_item_key

    gated_keys = {resource_item_key(match) for match in gated}
    filtered_logs = [entry for entry in log_entries if str(entry.get("key") or "") in gated_keys]
    return gated, filtered_logs

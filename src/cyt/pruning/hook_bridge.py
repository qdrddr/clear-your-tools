"""Hook-side bridge into the shared pruning coordinator."""

from __future__ import annotations

import contextlib
from typing import Any

from cyt.common.phase_timing import PhaseTimer, merge_phase_timings
from cyt.config import skills_enabled, uses_mcpc_tool_catalog
from cyt.indexer.build import anthropic_tools_to_catalog_entries
from cyt.mcpc.help_skill import append_mcpc_help_skill_entries
from cyt.mcpc.session_resources import append_mcpc_session_resource_entries
from cyt.mcpc.session_skills import append_mcpc_session_skill_entries
from cyt.proxy.anthropic import PruneResult
from cyt.pruners.remote import PrunerSettingsCache
from cyt.pruning.coordinator import ToolSource, coordinate_skills_tools_prune
from cyt.skills.client_skills import build_registry_for_hook_payload
from cyt.skills.executor_skill import append_executor_skill_entries
from cyt.skills.hook_quiet import hook_safe_stdout
from cyt.skills.search import MatchedSkill, eligible_skills_after_gate
from cyt.tools.mcpc_prune import mcpc_tools_to_catalog_entries
from cyt.tools.registry import load_tool_catalog

_SOURCE_ORDER = ("mcpc", "cloudflare", "executor", "definitions")


def _append_mcpc_skill_resource_entries(
    entries: list[Any],
    config: dict[str, Any],
) -> list[Any]:
    if not uses_mcpc_tool_catalog(config) or not skills_enabled(config):
        return entries
    merged = append_mcpc_help_skill_entries(entries, config)
    merged = append_mcpc_session_skill_entries(merged, config)
    return append_mcpc_session_resource_entries(merged, config)


def _hook_tool_sources(catalog: list[dict[str, Any]], config: dict[str, Any]) -> list[ToolSource]:
    by_source = {
        "mcpc": [tool for tool in catalog if tool.get("cyt_catalog_source") == "mcpc"],
        "cloudflare": [tool for tool in catalog if tool.get("cyt_catalog_source") == "cloudflare"],
        "executor": [tool for tool in catalog if tool.get("cyt_catalog_source") == "executor"],
        "definitions": [
            tool for tool in catalog if tool.get("cyt_catalog_source") == "definitions"
        ],
    }
    if not any(by_source.values()):
        by_source = {"root": catalog}

    tool_sources: list[ToolSource] = []
    for source_id in _SOURCE_ORDER:
        tools = by_source.get(source_id) or []
        if not tools:
            continue
        if source_id == "mcpc":
            tool_sources.append(
                ToolSource(
                    source_id,
                    tools,
                    tools_to_catalog_entries=mcpc_tools_to_catalog_entries,
                ),
            )
        else:
            tool_sources.append(
                ToolSource(
                    source_id,
                    tools,
                    tools_to_catalog_entries=anthropic_tools_to_catalog_entries,
                ),
            )
    if not tool_sources and by_source.get("root"):
        tool_sources.append(ToolSource("root", by_source["root"]))
    return tool_sources


def _aggregate_hook_prune_metrics(
    results: dict[str, PruneResult],
) -> tuple[list[dict[str, Any]], int, int, int, int]:
    all_tools: list[dict[str, Any]] = []
    tools_in = 0
    mcp_tools_in = 0
    tokens_in = 0
    tokens_out = 0
    for source_id in _SOURCE_ORDER:
        result = results.get(source_id)
        if result is None:
            continue
        if result.tools:
            all_tools.extend(result.tools)
        tools_in += result.tools_in or 0
        mcp_tools_in += result.mcp_tools_in or 0
        tokens_in += result.tokens_in or 0
        if result.tokens_out is not None:
            tokens_out += result.tokens_out
    return all_tools, tools_in, mcp_tools_in, tokens_in, tokens_out


def _failure_status_from_hook_prune_results(
    results: dict[str, PruneResult],
) -> tuple[str, str | None]:
    status = "applied"
    error: str | None = None
    for source_id in _SOURCE_ORDER:
        result = results.get(source_id)
        if result is None:
            continue
        if result.status != "applied":
            status = result.status
        if result.error:
            error = result.error
    return status, error


def _merge_hook_prune_results(results: dict[str, PruneResult]) -> PruneResult | None:
    if not results:
        return None
    if len(results) == 1:
        return next(iter(results.values()))

    all_tools, tools_in, mcp_tools_in, tokens_in, tokens_out = _aggregate_hook_prune_metrics(
        results,
    )
    status = "applied"
    error: str | None = None
    if not all_tools:
        status, error = _failure_status_from_hook_prune_results(results)

    return PruneResult(
        tools=all_tools or None,
        status=status,
        query=next(iter(results.values())).query,
        tools_in=tools_in,
        mcp_tools_in=mcp_tools_in,
        tools_out=len(all_tools) if all_tools else None,
        error=error,
        tokens_in=tokens_in,
        tokens_out=tokens_out if tokens_out else None,
        tokens_saved=tokens_in - tokens_out if tokens_out else None,
    )


def run_hook_coordinated_prune(
    query: str,
    config: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    skills_allowed: bool,
    tools_allowed: bool,
    skills_max_tokens: int | None = None,
    io_guarded: bool = False,
    pruner_settings: PrunerSettingsCache | None = None,
) -> tuple[
    PruneResult | None,
    list[MatchedSkill] | None,
    list[dict[str, Any]] | None,
    dict[str, PruneResult],
    dict[str, Any],
]:
    hook_timer = PhaseTimer()
    catalog = None
    if tools_allowed:
        with hook_timer.measure("hook:catalog-load"):
            catalog = load_tool_catalog(config)
    if tools_allowed and catalog is None:
        missing = PruneResult(
            tools=None,
            status="skipped",
            query=query,
            tools_in=0,
            mcp_tools_in=0,
            tools_out=None,
            error="missing catalog",
        )
        return missing, None, None, {}, merge_phase_timings(hook_timer)

    with hook_timer.measure("hook:skill-registry"):
        base_entries = build_registry_for_hook_payload(config, payload) if skills_allowed else []
        all_entries = append_executor_skill_entries(base_entries, config)
        all_entries = _append_mcpc_skill_resource_entries(all_entries, config)
        skill_entries = (
            eligible_skills_after_gate(query, all_entries, config=config) if all_entries else []
        )

    tool_sources = _hook_tool_sources(catalog, config) if tools_allowed and catalog else []

    if not tool_sources and not skill_entries:
        return None, None, catalog, {}, merge_phase_timings(hook_timer)

    skill_out: dict[str, Any] = {}
    stdout_guard = contextlib.nullcontext() if io_guarded else hook_safe_stdout()
    with stdout_guard:
        coordinated = coordinate_skills_tools_prune(
            query,
            config,
            tool_sources,
            skill_entries=skill_entries or None,
            for_hook=True,
            skills_allowed=skills_allowed,
            tools_allowed=bool(tool_sources),
            skills_max_tokens=skills_max_tokens,
            skill_out=skill_out,
            pruner_settings=pruner_settings,
            phase_timer=hook_timer,
        )

    prune_result = _merge_hook_prune_results(coordinated.prune_results) if tool_sources else None
    return (
        prune_result,
        coordinated.skill_matches,
        catalog,
        coordinated.prune_results,
        merge_phase_timings(hook_timer),
    )

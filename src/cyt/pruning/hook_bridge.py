"""Hook-side bridge into the shared pruning coordinator."""

from __future__ import annotations

import contextlib
from typing import Any

from cyt.config import skills_enabled, uses_mcpc_tool_catalog
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
from cyt.tools.registry import load_tool_catalog


def _append_mcpc_skill_resource_entries(
    entries: list[Any],
    config: dict[str, Any],
) -> list[Any]:
    if not uses_mcpc_tool_catalog(config) or not skills_enabled(config):
        return entries
    merged = append_mcpc_help_skill_entries(entries, config)
    merged = append_mcpc_session_skill_entries(merged, config)
    return append_mcpc_session_resource_entries(merged, config)


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
) -> tuple[PruneResult | None, list[MatchedSkill] | None, list[dict[str, Any]] | None]:
    catalog = load_tool_catalog(config) if tools_allowed else None
    if tools_allowed and catalog is None:
        return (
            PruneResult(
                tools=None,
                status="skipped",
                query=query,
                tools_in=0,
                mcp_tools_in=0,
                tools_out=None,
                error="missing catalog",
            ),
            None,
            None,
        )

    base_entries = build_registry_for_hook_payload(config, payload) if skills_allowed else []
    all_entries = append_executor_skill_entries(base_entries, config)
    all_entries = _append_mcpc_skill_resource_entries(all_entries, config)
    skill_entries = (
        eligible_skills_after_gate(query, all_entries, config=config) if all_entries else []
    )

    tool_sources: list[ToolSource] = []
    if tools_allowed and catalog:
        tool_sources.append(ToolSource("root", catalog))

    if not tool_sources and not skill_entries:
        return None, None, catalog

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
        )

    prune_result = coordinated.prune_results.get("root") if tool_sources else None
    return prune_result, coordinated.skill_matches, catalog

"""Hook-side bridge into the shared pruning coordinator."""

from __future__ import annotations

import contextlib
from typing import Any

from cyt.proxy.anthropic import PruneResult
from cyt.pruners.remote import PrunerSettingsCache
from cyt.pruning.coordinator import ToolSource, coordinate_skills_tools_prune
from cyt.skills.client_skills import build_registry_for_hook_payload
from cyt.skills.hook_quiet import hook_safe_stdout
from cyt.skills.search import MatchedSkill, eligible_skills_after_gate
from cyt.tools.registry import load_tool_catalog


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

    skill_entries: list[Any] = []
    if skills_allowed:
        skill_entries = eligible_skills_after_gate(
            query,
            build_registry_for_hook_payload(config, payload),
            config=config,
        )

    tool_sources: list[ToolSource] = []
    if tools_allowed and catalog:
        tool_sources.append(ToolSource("root", catalog))

    if not tool_sources and not skills_allowed:
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

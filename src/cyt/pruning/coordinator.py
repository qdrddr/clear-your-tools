"""Shared coordinator for parallel skills+tools pruning."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from cyt.config import (
    effective_pruning_pipeline,
    effective_skills_pipeline,
    skills_enabled,
)
from cyt.pruners.remote import PrunerSettingsCache
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.pruning.context import (
    MAX_PRUNE_BATCH_WORKERS,
    PruneContext,
    SkillsStage,
    WorkUnit,
)
from cyt.pruning.parallel import run_parallel
from cyt.skills.search import MatchedSkill
from cyt_core.types.prune import PruneResult

__all__ = [
    "MAX_PRUNE_BATCH_WORKERS",
    "CoordinateResult",
    "ToolSource",
    "build_prune_plan",
    "coordinate_skills_tools_prune",
    "prepare_prune_context",
    "run_prune_plan",
]


@dataclass
class ToolSource:
    source_id: str
    tools: list[dict[str, Any]]
    merged_to_api_tools: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None
    tools_to_catalog_entries: (
        Callable[
            [list[dict[str, Any]]],
            tuple[list[dict[str, Any]], list[Any]],
        ]
        | None
    ) = None


@dataclass
class CoordinateResult:
    prune_results: dict[str, PruneResult] = field(default_factory=dict)
    skill_matches: list[MatchedSkill] | None = None
    mcp_for_inject: list[dict[str, Any]] = field(default_factory=list)


def prepare_prune_context(
    query: str | None,
    config: dict[str, Any] | None,
    *,
    upstream_kind: str | None = None,
    tool_count: int = 0,
    eligible_count: int = 0,
    skill_entries: list[Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    skills_allowed: bool | None = None,
    tools_allowed: bool | None = None,
    for_hook: bool = False,
    tools_pipeline_override: list[str] | None = None,
) -> PruneContext | None:
    if config is None or not query:
        return None

    from cyt.tools.budget import tools_inject_allowed

    resolved_skills_allowed = (
        skills_allowed if skills_allowed is not None else skills_enabled(config)
    )
    resolved_tools_allowed = (
        tools_allowed
        if tools_allowed is not None
        else tools_inject_allowed(config, "hook" if for_hook else "proxy")
    )
    if not resolved_skills_allowed and not resolved_tools_allowed:
        return None

    ctx = PruneContext(
        query=query,
        config=config,
        skill_entries=list(skill_entries or []),
        pruner_settings=pruner_settings,
        tools_effective=effective_pruning_pipeline(
            config,
            tool_count,
            configured_pipeline=tools_pipeline_override,
        ),
        skills_effective=cast(
            SkillsStage,
            effective_skills_pipeline(config, eligible_count),
        ),
        skills_allowed=resolved_skills_allowed,
        tools_allowed=resolved_tools_allowed,
        upstream_kind=upstream_kind,
    )
    return ctx


def _skills_resolved(ctx: PruneContext) -> bool:
    matches = ctx.skill_out.get("matches")
    return isinstance(matches, list)


def _tool_units_for_stage(
    source_ids: list[str],
    *,
    kind: str,
    stage: SkillsStage | None = None,
    pipeline: tuple[str, ...] | None = None,
) -> list[WorkUnit]:
    units: list[WorkUnit] = []
    for source_id in source_ids:
        if kind == "tools_stage":
            units.append(
                WorkUnit(
                    kind="tools_stage",
                    source_id=source_id,
                    stage=stage,
                    pipeline=pipeline,
                ),
            )
        else:
            units.append(
                WorkUnit(
                    kind="tools_pipeline",
                    source_id=source_id,
                    pipeline=pipeline,
                ),
            )
    return units


def build_prune_plan(
    ctx: PruneContext,
    *,
    tool_sources: list[ToolSource],
) -> list[list[WorkUnit]]:
    """Build staged work units from effective pipelines."""
    source_ids = [source.source_id for source in tool_sources]
    stages: list[list[WorkUnit]] = []

    if not ctx.tools_allowed and ctx.skills_allowed and not _skills_resolved(ctx):
        stages.append([WorkUnit(kind="skills_search", stage=ctx.skills_effective)])
        return stages

    if ctx.tools_allowed and not ctx.skills_allowed:
        tool_units = _tool_units_for_stage(
            source_ids,
            kind="tools_pipeline",
            pipeline=tuple(ctx.tools_effective),
        )
        if ctx.skill_entries and not _skills_resolved(ctx):
            stages.append(
                [
                    *tool_units,
                    WorkUnit(kind="skills_search", stage=ctx.skills_effective),
                ],
            )
        else:
            stages.append(tool_units)
        return stages

    if not ctx.tools_allowed or not ctx.skills_allowed:
        return stages

    return _plan_both_skills_and_tools(ctx, source_ids)


def _plan_both_skills_and_tools(
    ctx: PruneContext,
    source_ids: list[str],
) -> list[list[WorkUnit]]:
    stages: list[list[WorkUnit]] = []
    tools_eff = ctx.tools_effective
    skills_eff = ctx.skills_effective

    if tools_eff == ["bm25"] and skills_eff == "bm25" and not _skills_resolved(ctx):
        stages.append(
            [
                *_tool_units_for_stage(
                    source_ids,
                    kind="tools_stage",
                    stage="bm25",
                    pipeline=("bm25",),
                ),
                WorkUnit(kind="skills_search", stage="bm25"),
            ],
        )
        return stages

    if tools_eff == ["rerank"] and skills_eff == "rerank" and not _skills_resolved(ctx):
        stages.append(
            [
                *_tool_units_for_stage(
                    source_ids,
                    kind="tools_stage",
                    stage="rerank",
                    pipeline=("rerank",),
                ),
                WorkUnit(kind="skills_search", stage="rerank"),
            ],
        )
        return stages

    if tools_eff == ["llm"] and skills_eff == "llm" and not _skills_resolved(ctx):
        stages.append(
            [
                *_tool_units_for_stage(
                    source_ids,
                    kind="tools_stage",
                    stage="llm",
                    pipeline=("llm",),
                ),
                WorkUnit(kind="skills_search", stage="llm"),
            ],
        )
        return stages

    if tools_eff == ["bm25"] and skills_eff in ("rerank", "llm") and not _skills_resolved(ctx):
        stages.append(
            [
                *_tool_units_for_stage(
                    source_ids,
                    kind="tools_stage",
                    stage="bm25",
                    pipeline=("bm25",),
                ),
                WorkUnit(kind="skills_search", stage=skills_eff),
            ],
        )
        return stages

    if skills_eff == "bm25" and len(tools_eff) == 1 and tools_eff[0] in ("rerank", "llm"):
        stages.append(
            [
                *_tool_units_for_stage(
                    source_ids,
                    kind="tools_pipeline",
                    pipeline=tuple(tools_eff),
                ),
                WorkUnit(kind="skills_search", stage="bm25"),
            ],
        )
        return stages

    if tools_eff == ["bm25", "rerank"] and skills_eff == "rerank" and not _skills_resolved(ctx):
        stages.append(
            [
                *_tool_units_for_stage(
                    source_ids,
                    kind="tools_stage",
                    stage="bm25",
                    pipeline=("bm25",),
                ),
                WorkUnit(kind="skills_search", stage="rerank"),
            ],
        )
        stages.append(
            _tool_units_for_stage(
                source_ids,
                kind="tools_stage",
                stage="rerank",
                pipeline=("rerank",),
            ),
        )
        return stages

    if tools_eff == ["bm25", "llm"] and skills_eff == "llm" and not _skills_resolved(ctx):
        stages.append(
            [
                *_tool_units_for_stage(
                    source_ids,
                    kind="tools_stage",
                    stage="bm25",
                    pipeline=("bm25",),
                ),
                WorkUnit(kind="skills_search", stage="llm"),
            ],
        )
        stages.append(
            _tool_units_for_stage(
                source_ids,
                kind="tools_stage",
                stage="llm",
                pipeline=("llm",),
            ),
        )
        return stages

    if not _skills_resolved(ctx):
        stages.append(
            [
                *_tool_units_for_stage(
                    source_ids,
                    kind="tools_pipeline",
                    pipeline=tuple(tools_eff),
                ),
                WorkUnit(kind="skills_search", stage=skills_eff),
            ],
        )
    else:
        stages.append(
            _tool_units_for_stage(
                source_ids,
                kind="tools_pipeline",
                pipeline=tuple(tools_eff),
            ),
        )
    return stages


def _source_map(tool_sources: list[ToolSource]) -> dict[str, ToolSource]:
    return {source.source_id: source for source in tool_sources}


def _run_skills_search(
    ctx: PruneContext,
    stage: SkillsStage,
    *,
    max_tokens: int | None,
) -> list[MatchedSkill]:
    from cyt.skills.proxy_inject import resolve_skills_for_query

    return resolve_skills_for_query(
        ctx.query,
        ctx.config,
        max_tokens=max_tokens,
        upstream_kind=ctx.upstream_kind,
        pruner_settings=ctx.pruner_settings,
        entries=ctx.skill_entries or None,
        skip_frontmatter_gate=bool(ctx.skill_entries),
    )


def _run_tools_filter(
    ctx: PruneContext,
    source: ToolSource,
    *,
    pipeline: list[str],
    for_hook: bool,
    capture_decomposed_catalog: bool,
) -> PruneResult:
    return filter_tools_for_query(
        source.tools,
        ctx.query,
        list(pipeline),
        capture_decomposed_catalog=capture_decomposed_catalog,
        merged_to_api_tools=source.merged_to_api_tools,
        config=ctx.config,
        pruner_settings=ctx.pruner_settings,
        for_hook=for_hook,
        tools_to_catalog_entries=source.tools_to_catalog_entries,
        catalog_bulk_id=source.source_id if for_hook else None,
    )


def _unit_key(unit: WorkUnit) -> str:
    stage = unit.stage or "pipeline"
    pipeline = "-".join(unit.pipeline or ())
    return f"{unit.kind}:{unit.source_id}:{stage}:{pipeline}"


def _register_plan_unit(
    work: dict[str, Callable[[], Any]],
    unit: WorkUnit,
    ctx: PruneContext,
    sources: dict[str, ToolSource],
    *,
    for_hook: bool,
    capture_decomposed_catalog: bool,
    skills_max_tokens: int | None,
) -> None:
    key = _unit_key(unit)
    if unit.kind == "skills_search":
        skills_stage = unit.stage or ctx.skills_effective

        def _skills_fn() -> list[MatchedSkill]:
            return _run_skills_search(ctx, skills_stage, max_tokens=skills_max_tokens)

        work[key] = _skills_fn
        return

    source = sources[unit.source_id]
    pipeline = list(unit.pipeline or ctx.tools_effective)
    if unit.kind == "tools_stage":

        def _tools_stage_fn() -> PruneResult:
            return _run_tools_filter(
                ctx,
                source,
                pipeline=pipeline,
                for_hook=for_hook,
                capture_decomposed_catalog=capture_decomposed_catalog,
            )

        work[key] = _tools_stage_fn
        return

    def _tools_pipeline_fn() -> PruneResult:
        return _run_tools_filter(
            ctx,
            source,
            pipeline=pipeline,
            for_hook=for_hook,
            capture_decomposed_catalog=capture_decomposed_catalog,
        )

    work[key] = _tools_pipeline_fn


def _apply_stage_results(
    stage_units: list[WorkUnit],
    stage_results: dict[str, Any],
    ctx: PruneContext,
    sources: dict[str, ToolSource],
    result: CoordinateResult,
    *,
    mcp_from_pruned: Callable[[PruneResult, ToolSource], list[dict[str, Any]]] | None,
) -> None:
    for unit in stage_units:
        key = _unit_key(unit)
        value = stage_results[key]
        if unit.kind == "skills_search":
            ctx.skill_out["matches"] = value
            result.skill_matches = value
            continue

        prune_result = cast(PruneResult, value)
        result.prune_results[unit.source_id] = prune_result
        if mcp_from_pruned is not None:
            result.mcp_for_inject.extend(mcp_from_pruned(prune_result, sources[unit.source_id]))


def run_prune_plan(
    plan: list[list[WorkUnit]],
    ctx: PruneContext,
    tool_sources: list[ToolSource],
    *,
    for_hook: bool = False,
    capture_decomposed_catalog: bool = False,
    skills_max_tokens: int | None = None,
    mcp_from_pruned: Callable[[PruneResult, ToolSource], list[dict[str, Any]]] | None = None,
) -> CoordinateResult:
    sources = _source_map(tool_sources)
    result = CoordinateResult()

    for stage_units in plan:
        work: dict[str, Callable[[], Any]] = {}
        for unit in stage_units:
            _register_plan_unit(
                work,
                unit,
                ctx,
                sources,
                for_hook=for_hook,
                capture_decomposed_catalog=capture_decomposed_catalog,
                skills_max_tokens=skills_max_tokens,
            )

        stage_results = run_parallel(work, max_workers=MAX_PRUNE_BATCH_WORKERS)
        _apply_stage_results(
            stage_units,
            stage_results,
            ctx,
            sources,
            result,
            mcp_from_pruned=mcp_from_pruned,
        )

    if result.skill_matches is None and _skills_resolved(ctx):
        result.skill_matches = cast(list[MatchedSkill], ctx.skill_out.get("matches"))
    return result


def coordinate_skills_tools_prune(
    query: str,
    config: dict[str, Any],
    tool_sources: list[ToolSource],
    *,
    skill_entries: list[Any] | None = None,
    upstream_kind: str | None = None,
    for_hook: bool = False,
    capture_decomposed_catalog: bool = False,
    skills_allowed: bool | None = None,
    tools_allowed: bool | None = None,
    skills_max_tokens: int | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    mcp_from_pruned: Callable[[PruneResult, ToolSource], list[dict[str, Any]]] | None = None,
    tools_pipeline_override: list[str] | None = None,
    skill_out: dict[str, Any] | None = None,
) -> CoordinateResult:
    tool_count = max((len(source.tools) for source in tool_sources), default=0)
    eligible_count = len(skill_entries or [])
    ctx = prepare_prune_context(
        query,
        config,
        upstream_kind=upstream_kind,
        tool_count=tool_count,
        eligible_count=eligible_count,
        skill_entries=skill_entries,
        pruner_settings=pruner_settings,
        skills_allowed=skills_allowed,
        tools_allowed=tools_allowed,
        for_hook=for_hook,
        tools_pipeline_override=tools_pipeline_override,
    )
    if ctx is None:
        return CoordinateResult()

    if skill_entries:
        ctx.skill_entries = list(skill_entries)
    if skill_out is not None:
        ctx.skill_out = skill_out

    plan = build_prune_plan(ctx, tool_sources=tool_sources)
    if not plan:
        return CoordinateResult()

    return run_prune_plan(
        plan,
        ctx,
        tool_sources,
        for_hook=for_hook,
        capture_decomposed_catalog=capture_decomposed_catalog,
        skills_max_tokens=skills_max_tokens,
        mcp_from_pruned=mcp_from_pruned,
    )

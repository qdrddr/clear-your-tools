"""Tool catalog pruning pipeline for proxy, hook, and coordinator paths."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any, TypedDict, cast

from cyt.common.phase_timing import PhaseTimer
from cyt.common.token_usage import StageTokenUsage
from cyt.config import (
    effective_pruning_pipeline,
    llm_minimum_tools,
    load_config,
    pruning_pipeline_from_config,
    pruning_stage_model_nick,
)
from cyt.indexer.build import (
    anthropic_tools_to_catalog_entries,
    catalog_tool_count,
)
from cyt.indexer.tokens import count_json_tokens
from cyt.pruners.bm25 import bm25_catalog_dict, prune_bm25_catalog
from cyt.pruners.catalog_common import finalize_scored_stage
from cyt.pruners.llm import (
    llm_catalog_dict,
    prune_llm_catalog,
    trim_catalog_dict,
)
from cyt.pruners.policies import (
    PolicyContext,
    batch_tool_pass_through,
    catalog_needs_partition,
    entries_for_policy,
    merge_catalog,
    merge_tools_preserving_order,
    output_policy_context_from_config,
    partition_catalog,
    policy_context_from_config,
    request_pass_through,
    tools_for_catalog,
)
from cyt.pruners.query import tools_pruning_query
from cyt.pruners.remote import PrunerSettingsCache
from cyt.pruners.rerank import prune_reranked_catalog, rerank_catalog_dict
from cyt.tools.budget import tools_inject_allowed
from cyt.tools.policy_context import prepare_hook_tool_pruning
from cyt_core.types.prune import PruneResult

logger = logging.getLogger(__name__)

# Initial LLM pruning attempt plus two retries before rerank/BM25 fallback.
LLM_STAGE_MAX_ATTEMPTS = 3

__all__ = [
    "LLM_STAGE_MAX_ATTEMPTS",
    "filter_tools_for_query",
    "merge_api_tool_onto_original",
]


def _merged_tools_to_anthropic(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in merged:
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        tool_name = tool.get("name", "")
        out.append(
            {
                "name": tool_name,
                "description": tool.get("description", ""),
                "input_schema": schema,
            },
        )
    return out


_TOOL_SCHEMA_KEYS = ("parameters", "input_schema", "inputSchema")


def _schema_value_from_api(api_tool: dict[str, Any], schema_key: str) -> object | None:
    if schema_key in api_tool:
        return cast(object, api_tool[schema_key])
    if schema_key == "parameters" and "input_schema" in api_tool:
        return cast(object, api_tool["input_schema"])
    if schema_key in ("input_schema", "inputSchema") and "parameters" in api_tool:
        return cast(object, api_tool["parameters"])
    return None


def merge_api_tool_onto_original(
    original: dict[str, Any],
    api_tool: dict[str, Any],
) -> dict[str, Any]:
    """Preserve all incoming tool root keys; overlay only pruned description/schema fields."""
    out = copy.deepcopy(original)
    if "description" in api_tool:
        out["description"] = api_tool["description"]
    for schema_key in _TOOL_SCHEMA_KEYS:
        if schema_key not in original:
            continue
        schema_value = _schema_value_from_api(api_tool, schema_key)
        if schema_value is not None:
            out[schema_key] = schema_value
    return out


def _original_tools_by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(t.get("name", "")): t for t in tools if isinstance(t, dict) and t.get("name")}


def _pruned_tools_by_name(
    original_tools: list[dict[str, Any]],
    merged: list[dict[str, Any]],
    to_api: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    original_by_name = _original_tools_by_name(original_tools)
    pruned_by_name: dict[str, dict[str, Any]] = {}
    for tool in to_api(merged):
        name = str(tool.get("name", ""))
        if not name:
            continue
        original_tool = original_by_name.get(name)
        pruned_by_name[name] = (
            merge_api_tool_onto_original(original_tool, tool)
            if original_tool
            else copy.deepcopy(tool)
        )
    return pruned_by_name


def _run_catalog_pruning(
    entries: list[dict[str, Any]],
    enums: list[Any],
    query: str,
    configured_pipeline: list[str] | None,
    capture_decomposed_catalog: bool,
    ctx: PolicyContext,
    output_ctx: PolicyContext | None = None,
    skill_entries: list[Any] | None = None,
    skill_llm_out: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    *,
    for_hook: bool = False,
    upstream_kind: str | None = None,
    catalog_bulk_id: str | None = None,
    phase_timer: PhaseTimer | None = None,
    phase_prefix: str = "tools",
) -> tuple[
    list[dict[str, Any]],
    dict[str, int],
    dict[str, dict[str, int]],
    dict[str, dict[str, Any]] | None,
    dict[str, StageTokenUsage],
    dict[str, int],
    int,
    int,
]:
    decomposed: dict[str, int] = {}
    decomposed_breakdown: dict[str, dict[str, int]] = {}
    decomposed_catalog: dict[str, dict[str, Any]] | None = None
    pruning_token_usage: dict[str, StageTokenUsage] = {}
    tool_properties_count_in = 0
    tool_properties_count_out = 0
    resolved_config = config or load_config()
    for_proxy = not for_hook
    from cyt.tools.catalog_cache import (
        catalog_snapshot_from_cache,
        ensure_tool_catalog_cached,
        get_tool_catalog_cache,
    )

    if for_hook and catalog_bulk_id:
        catalog_ctx = (
            phase_timer.measure(f"{phase_prefix}:catalog", bulk_id=catalog_bulk_id)
            if phase_timer is not None
            else nullcontext()
        )
        with catalog_ctx:
            cached = get_tool_catalog_cache(
                catalog_bulk_id,
                entries,
                enums,
                resolved_config,
                blocking=False,
            )
    else:
        catalog_ctx = (
            phase_timer.measure(f"{phase_prefix}:catalog")
            if phase_timer is not None
            else nullcontext()
        )
        with catalog_ctx:
            cached = ensure_tool_catalog_cached(
                entries,
                enums,
                resolved_config,
                bulk_id=catalog_bulk_id or "",
            )
    catalog_snapshot = catalog_snapshot_from_cache(cached)
    build_catalog = cached.catalog
    data = build_catalog
    index = cached.index
    pipeline = effective_pruning_pipeline(
        resolved_config,
        catalog_tool_count(data),
        configured_pipeline=configured_pipeline,
    )
    terminal_stage = pipeline[-1] if pipeline else None
    reinstate_ctx = output_ctx or output_policy_context_from_config(
        resolved_config,
        terminal_stage=terminal_stage,
    )
    if for_hook:
        prepare_hook_tool_pruning(resolved_config, ctx, output_ctx, reinstate_ctx)
    if pipeline == ["bm25"] and skill_entries is None:
        from cyt.common.bm25_constants import configure_sdk_bm25_defaults
        from cyt.config import bm25_prune_enums, bm25_score_tool, bm25_score_tool_enum
        from cyt.indexer.pipeline import prune_catalog_bm25_and_retrieve
        from cyt.pruners.bm25 import bm25_stage_usage

        configure_sdk_bm25_defaults(resolved_config)
        composite = prune_catalog_bm25_and_retrieve(
            data,
            build_catalog,
            catalog_snapshot.pipeline_catalog_index(),
            query,
            ctx,
            reinstate_ctx,
            options={
                "score_tool": bm25_score_tool(resolved_config),
                "score_tool_enum": bm25_score_tool_enum(resolved_config),
                "prune_enums": bm25_prune_enums(resolved_config),
                "pipeline": pipeline,
            },
        )
        merged = composite.get("tools", [])
        if not isinstance(merged, list):
            merged = []
        decomposed = {str(k): int(v) for k, v in dict(composite.get("decomposed", {})).items()}
        decomposed_breakdown = {
            str(stage): {str(k): int(v) for k, v in dict(counts).items()}
            for stage, counts in dict(composite.get("decomposed_breakdown", {})).items()
        }
        tool_properties_count_in = int(
            composite.get("optional_chunk_count_in", tool_properties_count_in),
        )
        tool_properties_count_out = int(composite.get("optional_chunk_count_out", 0))
        pruning_token_usage = {"bm25": bm25_stage_usage()}
        decomposed_catalog = (
            {"build_index": _snapshot_catalog(build_catalog), "bm25": _snapshot_catalog(data)}
            if capture_decomposed_catalog
            else None
        )
        pruning_model_tokens = _pruning_tokens_summary(pruning_token_usage)
        return (
            merged,
            decomposed,
            decomposed_breakdown,
            decomposed_catalog,
            pruning_token_usage,
            pruning_model_tokens,
            tool_properties_count_in,
            tool_properties_count_out,
        )
    tool_properties_count_in = _count_optional_property_chunks(data)
    (
        data,
        decomposed,
        decomposed_breakdown,
        post_rerank,
        post_rerank_scored,
        pinned,
        decomposed_catalog,
        pruning_token_usage,
    ) = _run_pruning_pipeline(
        data,
        query,
        pipeline,
        capture_catalog=capture_decomposed_catalog,
        ctx=ctx,
        skill_entries=skill_entries,
        skill_llm_out=skill_llm_out,
        config=resolved_config,
        pruner_settings=pruner_settings,
        catalog_bulk_id=catalog_bulk_id,
        phase_timer=phase_timer,
        phase_prefix=phase_prefix,
        for_proxy=for_proxy,
    )
    pruning_model_tokens = _pruning_tokens_summary(pruning_token_usage)
    from cyt.indexer.pipeline import recompose_and_retrieve_tools

    pinned_for_recompose = pinned if pinned else None
    merged = recompose_and_retrieve_tools(
        data,
        build_catalog,
        index,
        post_rerank,
        post_rerank_scored,
        pinned_for_recompose,
        pipeline,
        ctx or policy_context_from_config(resolved_config, terminal_stage=terminal_stage),
        reinstate_ctx,
    )
    return (
        merged,
        decomposed,
        decomposed_breakdown,
        decomposed_catalog,
        pruning_token_usage,
        pruning_model_tokens,
        tool_properties_count_in,
        tool_properties_count_out,
    )


def _count_catalog_items(data: dict[str, Any]) -> int:
    json_n, md_n = _count_json_md(data)
    return json_n + md_n


def _count_json_md(data: dict[str, Any]) -> tuple[int, int]:
    json_items = data.get("json")
    md_items = data.get("md")
    json_n = len(json_items) if isinstance(json_items, list) else 0
    md_n = len(md_items) if isinstance(md_items, list) else 0
    return json_n, md_n


def _breakdown_entry(data: dict[str, Any]) -> dict[str, int]:
    json_n, md_n = _count_json_md(data)
    return {"json": json_n, "md": md_n}


def _count_optional_property_chunks(data: dict[str, Any]) -> int:
    from cyt.indexer.pipeline import classify_and_count_catalog

    result = classify_and_count_catalog(data)
    return int(result.get("optional_chunk_count", 0))


def _pruning_tokens_summary(usage_map: dict[str, StageTokenUsage]) -> dict[str, int]:
    """Per-stage input/output/reasoning counts (not summed totals)."""
    summary: dict[str, int] = {}
    for stage, usage in usage_map.items():
        if usage.input_tokens:
            summary[f"{stage}_in"] = usage.input_tokens
        if usage.output_tokens:
            summary[f"{stage}_out"] = usage.output_tokens
        if usage.reasoning_tokens:
            summary[f"{stage}_reasoning"] = usage.reasoning_tokens
    return summary


def _format_pruning_usage_log(usage_map: dict[str, StageTokenUsage]) -> str:
    parts: list[str] = []
    for stage, usage in usage_map.items():
        if not (usage.input_tokens or usage.output_tokens or usage.reasoning_tokens):
            continue
        detail_parts = [f"in={usage.input_tokens}", f"out={usage.output_tokens}"]
        if usage.reasoning_tokens:
            detail_parts.append(f"reasoning={usage.reasoning_tokens}")
        parts.append(f"{stage} ({', '.join(detail_parts)})")
    return ", ".join(parts)


def _snapshot_catalog(data: dict[str, Any]) -> dict[str, Any]:
    """Catalog snapshot for debug logs (json/md only; request tools live under pruning.input)."""
    snap = copy.deepcopy(data)
    snap.pop("tools", None)
    return snap


def _run_rerank_stage(
    data: dict[str, Any],
    query: str,
    *,
    capture_catalog: bool,
    snapshots: dict[str, dict[str, Any]] | None,
    decomposed_breakdown: dict[str, dict[str, int]],
    decomposed: dict[str, int],
    pruning_token_usage: dict[str, StageTokenUsage],
    skill_entries: list[Any] | None = None,
    skill_llm_out: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    ctx: PolicyContext | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    rerank_usage: StageTokenUsage
    rerank_settings = pruner_settings.for_stage("rerank") if pruner_settings else None
    skill_matches_resolved = skill_llm_out is not None and skill_llm_out.get("matches") is not None
    prune_skills_in_parallel = skill_entries is not None and not skill_matches_resolved
    if prune_skills_in_parallel:
        from cyt.config import skills_pipeline_uses_rerank
        from cyt.pruning.parallel import run_parallel
        from cyt.skills.catalog import SkillEntryRef
        from cyt.skills.rerank import rerank_skill_nodes
        from cyt.skills.search import MatchedSkill

        parallel_skill_entries = cast(list[SkillEntryRef], skill_entries)

        if skills_pipeline_uses_rerank(config):

            def _rerank_tools() -> tuple[dict[str, Any], dict[str, Any], StageTokenUsage]:
                scored, usage = rerank_catalog_dict(
                    data,
                    query,
                    ctx=ctx,
                    prune=False,
                    merge_pinned=False,
                    config=config,
                    settings=rerank_settings,
                )
                return (
                    prune_reranked_catalog(scored),
                    copy.deepcopy(scored),
                    usage,
                )

            def _rerank_skills() -> tuple[list[MatchedSkill], StageTokenUsage]:
                return rerank_skill_nodes(
                    query,
                    parallel_skill_entries,
                    config=config,
                    settings=rerank_settings,
                )

            parallel_results = run_parallel(
                {"tools": _rerank_tools, "skills": _rerank_skills},
            )
            tools_result = cast(
                tuple[dict[str, Any], dict[str, Any], StageTokenUsage],
                parallel_results["tools"],
            )
            skills_result = cast(
                tuple[list[MatchedSkill], StageTokenUsage],
                parallel_results["skills"],
            )
            data, post_rerank_scored, rerank_usage = tools_result
            skill_matches, skill_usage = skills_result
            rerank_usage = rerank_usage.merge(skill_usage)
            if skill_llm_out is not None:
                skill_llm_out["matches"] = skill_matches
        else:
            scored, rerank_usage = rerank_catalog_dict(
                data,
                query,
                ctx=ctx,
                prune=False,
                merge_pinned=False,
                config=config,
                settings=rerank_settings,
            )
            post_rerank_scored = copy.deepcopy(scored)
            data = prune_reranked_catalog(scored)
    else:
        scored, rerank_usage = rerank_catalog_dict(
            data,
            query,
            ctx=ctx,
            prune=False,
            merge_pinned=False,
            config=config,
            settings=rerank_settings,
        )
        post_rerank_scored = copy.deepcopy(scored)
        data = prune_reranked_catalog(scored)
    pruning_token_usage["rerank"] = rerank_usage
    if capture_catalog and snapshots is not None:
        snapshots["rerank"] = _snapshot_catalog(data)
    if capture_catalog and snapshots is not None:
        snapshots["rerank_pruned"] = _snapshot_catalog(data)
    post_rerank = copy.deepcopy(data)
    decomposed_breakdown["rerank"] = _breakdown_entry(data)
    decomposed["rerank"] = (
        decomposed_breakdown["rerank"]["json"] + decomposed_breakdown["rerank"]["md"]
    )
    return data, post_rerank, post_rerank_scored


def _run_llm_stage(
    data: dict[str, Any],
    query: str,
    *,
    trim_before_llm: bool,
    llm_prescore_before_trim: bool = False,
    capture_catalog: bool,
    snapshots: dict[str, dict[str, Any]] | None,
    decomposed_breakdown: dict[str, dict[str, int]],
    decomposed: dict[str, int],
    pruning_token_usage: dict[str, StageTokenUsage],
    skill_entries: list[Any] | None = None,
    skill_llm_out: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    ctx: PolicyContext | None = None,
    catalog_bulk_id: str | None = None,
    phase_timer: PhaseTimer | None = None,
    phase_prefix: str = "tools",
    for_proxy: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    if trim_before_llm or llm_prescore_before_trim:
        if llm_prescore_before_trim and not trim_before_llm:
            from cyt.pruners.bm25 import bm25_catalog_dict

            prescore_ctx = (
                phase_timer.measure(f"{phase_prefix}:prescore")
                if phase_timer is not None
                else nullcontext()
            )
            with prescore_ctx:
                scored, prescore_usage = bm25_catalog_dict(
                    data,
                    query,
                    ctx=ctx,
                    prune=False,
                    merge_pinned=False,
                    config=config,
                )
            pruning_token_usage["llm_prescore"] = prescore_usage
            data = scored
        data = trim_catalog_dict(data)

    llm_usage: StageTokenUsage
    llm_settings = pruner_settings.for_stage("llm") if pruner_settings else None
    skill_matches_resolved = skill_llm_out is not None and skill_llm_out.get("matches") is not None
    prune_skills_in_parallel = skill_entries is not None and not skill_matches_resolved
    if prune_skills_in_parallel:
        from cyt.config import skills_pipeline_uses_llm
        from cyt.pruning.parallel import run_parallel
        from cyt.skills.catalog import SkillEntryRef
        from cyt.skills.llm import llm_skill_nodes
        from cyt.skills.search import MatchedSkill

        parallel_skill_entries = cast(list[SkillEntryRef], skill_entries)

        catalog_count = catalog_tool_count(data)
        llm_min = llm_minimum_tools(config)
        if skills_pipeline_uses_llm(config) and catalog_count >= llm_min:

            def _llm_tools() -> tuple[dict[str, Any], dict[str, Any], StageTokenUsage]:
                return llm_catalog_dict(
                    data,
                    query,
                    ctx=ctx,
                    merge_pinned=False,
                    config=config,
                    settings=llm_settings,
                    catalog_bulk_id=catalog_bulk_id,
                    phase_timer=phase_timer,
                    phase_prefix=phase_prefix,
                    for_proxy=for_proxy,
                )

            def _llm_skills() -> tuple[list[MatchedSkill], StageTokenUsage]:
                return llm_skill_nodes(
                    query,
                    parallel_skill_entries,
                    config=config,
                    settings=llm_settings,
                )

            parallel_results = run_parallel({"tools": _llm_tools, "skills": _llm_skills})
            tools_result = cast(
                tuple[dict[str, Any], dict[str, Any], StageTokenUsage],
                parallel_results["tools"],
            )
            skills_result = cast(
                tuple[list[MatchedSkill], StageTokenUsage],
                parallel_results["skills"],
            )
            data, _, llm_usage = tools_result
            skill_matches, skill_usage = skills_result
            llm_usage = llm_usage.merge(skill_usage)
            if skill_llm_out is not None:
                skill_llm_out["matches"] = skill_matches
        else:
            data, _, llm_usage = llm_catalog_dict(
                data,
                query,
                ctx=ctx,
                merge_pinned=False,
                config=config,
                settings=llm_settings,
                catalog_bulk_id=catalog_bulk_id,
                phase_timer=phase_timer,
                phase_prefix=phase_prefix,
                for_proxy=for_proxy,
            )
    else:
        data, _, llm_usage = llm_catalog_dict(
            data,
            query,
            ctx=ctx,
            merge_pinned=False,
            config=config,
            settings=llm_settings,
            catalog_bulk_id=catalog_bulk_id,
            phase_timer=phase_timer,
            phase_prefix=phase_prefix,
            for_proxy=for_proxy,
        )

    stage_result = finalize_scored_stage(
        data,
        prune_fn=lambda catalog: prune_llm_catalog(catalog, config=config),
    )
    data = stage_result.data
    post_rerank = stage_result.post_rerank
    post_rerank_scored = stage_result.post_rerank_scored

    pruning_token_usage["llm"] = llm_usage
    decomposed_breakdown["llm"] = _breakdown_entry(data)
    decomposed["llm"] = decomposed_breakdown["llm"]["json"] + decomposed_breakdown["llm"]["md"]
    if capture_catalog and snapshots is not None:
        snapshots["llm"] = _snapshot_catalog(data)
    return data, post_rerank, post_rerank_scored


def _run_bm25_stage(
    data: dict[str, Any],
    query: str,
    *,
    capture_catalog: bool,
    snapshots: dict[str, dict[str, Any]] | None,
    decomposed_breakdown: dict[str, dict[str, int]],
    decomposed: dict[str, int],
    pruning_token_usage: dict[str, StageTokenUsage],
    ctx: PolicyContext | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    data, bm25_usage = bm25_catalog_dict(
        data,
        query,
        ctx=ctx,
        prune=False,
        merge_pinned=False,
    )
    pruning_token_usage["bm25"] = bm25_usage
    if capture_catalog and snapshots is not None:
        snapshots["bm25"] = _snapshot_catalog(data)
    post_rerank_scored = copy.deepcopy(data)
    data = prune_bm25_catalog(data)
    if capture_catalog and snapshots is not None:
        snapshots["bm25_pruned"] = _snapshot_catalog(data)
    post_rerank = copy.deepcopy(data)
    decomposed_breakdown["bm25"] = _breakdown_entry(data)
    decomposed["bm25"] = decomposed_breakdown["bm25"]["json"] + decomposed_breakdown["bm25"]["md"]
    return data, post_rerank, post_rerank_scored


class _StageKwargs(TypedDict):
    data: dict[str, Any]
    query: str
    capture_catalog: bool
    snapshots: dict[str, dict[str, Any]] | None
    decomposed_breakdown: dict[str, dict[str, int]]
    decomposed: dict[str, int]
    pruning_token_usage: dict[str, StageTokenUsage]
    skill_entries: list[Any] | None
    skill_llm_out: dict[str, Any] | None
    config: dict[str, Any] | None
    pruner_settings: PrunerSettingsCache | None
    ctx: PolicyContext | None
    for_proxy: bool


def _bm25_stage_kwargs(stage_kwargs: _StageKwargs) -> dict[str, Any]:
    return {
        "data": stage_kwargs["data"],
        "query": stage_kwargs["query"],
        "capture_catalog": stage_kwargs["capture_catalog"],
        "snapshots": stage_kwargs["snapshots"],
        "decomposed_breakdown": stage_kwargs["decomposed_breakdown"],
        "decomposed": stage_kwargs["decomposed"],
        "pruning_token_usage": stage_kwargs["pruning_token_usage"],
        "ctx": stage_kwargs["ctx"],
    }


def _rerank_stage_kwargs(stage_kwargs: _StageKwargs) -> dict[str, Any]:
    return {
        "data": stage_kwargs["data"],
        "query": stage_kwargs["query"],
        "capture_catalog": stage_kwargs["capture_catalog"],
        "snapshots": stage_kwargs["snapshots"],
        "decomposed_breakdown": stage_kwargs["decomposed_breakdown"],
        "decomposed": stage_kwargs["decomposed"],
        "pruning_token_usage": stage_kwargs["pruning_token_usage"],
        "skill_entries": stage_kwargs["skill_entries"],
        "skill_llm_out": stage_kwargs["skill_llm_out"],
        "config": stage_kwargs["config"],
        "pruner_settings": stage_kwargs["pruner_settings"],
        "ctx": stage_kwargs["ctx"],
    }


def _llm_stage_kwargs(stage_kwargs: _StageKwargs) -> dict[str, Any]:
    return {
        "data": stage_kwargs["data"],
        "query": stage_kwargs["query"],
        "capture_catalog": stage_kwargs["capture_catalog"],
        "snapshots": stage_kwargs["snapshots"],
        "decomposed_breakdown": stage_kwargs["decomposed_breakdown"],
        "decomposed": stage_kwargs["decomposed"],
        "pruning_token_usage": stage_kwargs["pruning_token_usage"],
        "skill_entries": stage_kwargs["skill_entries"],
        "skill_llm_out": stage_kwargs["skill_llm_out"],
        "config": stage_kwargs["config"],
        "pruner_settings": stage_kwargs["pruner_settings"],
        "ctx": stage_kwargs["ctx"],
    }


def _run_pipeline_stage(
    stage: str,
    *,
    stage_index: int,
    pruning_pipeline: list[str],
    data: dict[str, Any],
    query: str,
    capture_catalog: bool,
    snapshots: dict[str, dict[str, Any]] | None,
    decomposed_breakdown: dict[str, dict[str, int]],
    decomposed: dict[str, int],
    pruning_token_usage: dict[str, StageTokenUsage],
    skill_entries: list[Any] | None = None,
    skill_llm_out: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    ctx: PolicyContext | None = None,
    catalog_bulk_id: str | None = None,
    phase_timer: PhaseTimer | None = None,
    phase_prefix: str = "tools",
    for_proxy: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    stage_kwargs: _StageKwargs = {
        "data": data,
        "query": query,
        "capture_catalog": capture_catalog,
        "snapshots": snapshots,
        "decomposed_breakdown": decomposed_breakdown,
        "decomposed": decomposed,
        "pruning_token_usage": pruning_token_usage,
        "skill_entries": skill_entries,
        "skill_llm_out": skill_llm_out,
        "config": config,
        "pruner_settings": pruner_settings,
        "ctx": ctx,
        "for_proxy": for_proxy,
    }
    stage_ctx = (
        phase_timer.measure(f"{phase_prefix}:{stage}") if phase_timer is not None else nullcontext()
    )
    with stage_ctx:
        if stage == "rerank":
            try:
                return _run_rerank_stage(**_rerank_stage_kwargs(stage_kwargs))
            except Exception as exc:
                logger.warning("rerank failed, falling back to bm25: %s", exc)
                return _run_bm25_stage(**_bm25_stage_kwargs(stage_kwargs))
        if stage == "llm":
            prior_stage_scored = stage_index > 0 and pruning_pipeline[stage_index - 1] in (
                "rerank",
                "bm25",
            )
            llm_only_pipeline = len(pruning_pipeline) == 1 and pruning_pipeline[0] == "llm"
            for attempt in range(1, LLM_STAGE_MAX_ATTEMPTS + 1):
                try:
                    updated, post_rerank, post_rerank_scored = _run_llm_stage(
                        **_llm_stage_kwargs(stage_kwargs),
                        trim_before_llm=prior_stage_scored,
                        llm_prescore_before_trim=llm_only_pipeline and not prior_stage_scored,
                        catalog_bulk_id=catalog_bulk_id,
                        phase_timer=phase_timer,
                        phase_prefix=phase_prefix,
                        for_proxy=stage_kwargs["for_proxy"],
                    )
                    return updated, post_rerank, post_rerank_scored
                except Exception as exc:
                    if attempt < LLM_STAGE_MAX_ATTEMPTS:
                        logger.warning(
                            "llm pruning failed (attempt %d/%d), retrying: %s",
                            attempt,
                            LLM_STAGE_MAX_ATTEMPTS,
                            exc,
                        )
                    else:
                        logger.warning(
                            "llm pruning failed after %d attempts: %s",
                            LLM_STAGE_MAX_ATTEMPTS,
                            exc,
                        )
            resolved_config = config or load_config()
            if pruning_stage_model_nick(resolved_config, "rerank"):
                try:
                    logger.warning(
                        "llm pruning failed after %d attempts, trying rerank fallback",
                        LLM_STAGE_MAX_ATTEMPTS,
                    )
                    return _run_rerank_stage(**_rerank_stage_kwargs(stage_kwargs))
                except (Exception, SystemExit) as rerank_exc:
                    logger.warning(
                        "rerank fallback after llm failure failed, falling back to bm25: %s",
                        rerank_exc,
                    )
            else:
                logger.warning(
                    "llm pruning failed after %d attempts, falling back to bm25",
                    LLM_STAGE_MAX_ATTEMPTS,
                )
            return _run_bm25_stage(**_bm25_stage_kwargs(stage_kwargs))
        if stage == "bm25":
            return _run_bm25_stage(**_bm25_stage_kwargs(stage_kwargs))
        raise ValueError(f"unknown pruning stage: {stage}")


def _run_pruning_pipeline(
    data: dict[str, Any],
    query: str,
    pruning_pipeline: list[str],
    capture_catalog: bool = False,
    ctx: PolicyContext | None = None,
    skill_entries: list[Any] | None = None,
    skill_llm_out: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    catalog_bulk_id: str | None = None,
    phase_timer: PhaseTimer | None = None,
    phase_prefix: str = "tools",
    for_proxy: bool = False,
) -> tuple[
    dict[str, Any],
    dict[str, int],
    dict[str, dict[str, int]],
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any],
    dict[str, dict[str, Any]] | None,
    dict[str, StageTokenUsage],
]:
    decomposed_breakdown: dict[str, dict[str, int]] = {
        "build_index": _breakdown_entry(data),
    }
    decomposed: dict[str, int] = {
        "build_index": decomposed_breakdown["build_index"]["json"]
        + decomposed_breakdown["build_index"]["md"],
    }
    pruning_token_usage: dict[str, StageTokenUsage] = {}
    snapshots: dict[str, dict[str, Any]] | None = {} if capture_catalog else None
    post_rerank: dict[str, Any] | None = None
    post_rerank_scored: dict[str, Any] | None = None

    if capture_catalog and snapshots is not None:
        snapshots["build_index"] = _snapshot_catalog(data)

    resolved_config = config or load_config()
    terminal_stage = pruning_pipeline[-1] if pruning_pipeline else None
    policy_ctx = ctx or policy_context_from_config(
        resolved_config,
        terminal_stage=terminal_stage,
    )
    if not for_proxy:
        prepare_hook_tool_pruning(resolved_config, policy_ctx)
    pinned: dict[str, Any] = {}
    if catalog_needs_partition(data, policy_ctx):
        data, pinned = partition_catalog(data, policy_ctx)

    for i, stage in enumerate(pruning_pipeline):
        data, stage_post_rerank, stage_post_rerank_scored = _run_pipeline_stage(
            stage,
            stage_index=i,
            pruning_pipeline=pruning_pipeline,
            data=data,
            query=query,
            capture_catalog=capture_catalog,
            snapshots=snapshots,
            decomposed_breakdown=decomposed_breakdown,
            decomposed=decomposed,
            pruning_token_usage=pruning_token_usage,
            skill_entries=skill_entries,
            skill_llm_out=skill_llm_out,
            config=config,
            pruner_settings=pruner_settings,
            ctx=policy_ctx,
            catalog_bulk_id=catalog_bulk_id,
            phase_timer=phase_timer,
            phase_prefix=phase_prefix,
            for_proxy=for_proxy,
        )
        if stage_post_rerank is not None:
            post_rerank = stage_post_rerank
        if stage_post_rerank_scored is not None:
            post_rerank_scored = stage_post_rerank_scored

    if pinned:
        data = merge_catalog(data, pinned)

    if pruning_token_usage:
        if parts := _format_pruning_usage_log(pruning_token_usage):
            _log_operator_message(f"pruning model tokens: {parts}")

    return (
        data,
        decomposed,
        decomposed_breakdown,
        post_rerank,
        post_rerank_scored,
        pinned,
        snapshots,
        pruning_token_usage,
    )


def _log_operator_message(msg: str) -> None:
    """Mirror a message to the module logger, stdout, and the proxy debug log file."""
    from cyt.proxy.transport import append_debug_log_block, debug_endpoint_proxy_log_path

    logger.info(msg)
    print(msg, flush=True)
    if (log_path := debug_endpoint_proxy_log_path.get()) is not None:
        append_debug_log_block(log_path, label="operator", content=msg)


def _log_tool_token_counts(tokens_in: int, tokens_out: int | None) -> None:
    msg = f"tool tokens (compact JSON): input={tokens_in}"
    if tokens_out is not None:
        saved = tokens_in - tokens_out
        pct = (100.0 * saved / tokens_in) if tokens_in else 0.0
        msg += f", output={tokens_out}, saved={saved} ({pct:.1f}%)"
    _log_operator_message(msg)


def filter_tools_for_query(
    original_tools: list[dict[str, Any]],
    query: str,
    pruning_pipeline: list[str] | None = None,
    capture_decomposed_catalog: bool = False,
    ctx: PolicyContext | None = None,
    tools_to_catalog_entries: Callable[
        [list[dict[str, Any]]],
        tuple[list[dict[str, Any]], list[Any]],
    ]
    | None = None,
    merged_to_api_tools: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    skill_entries: list[Any] | None = None,
    skill_llm_out: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    *,
    for_hook: bool = False,
    upstream_kind: str | None = None,
    catalog_bulk_id: str | None = None,
    phase_timer: PhaseTimer | None = None,
    phase_prefix: str = "tools",
) -> PruneResult:
    tools_in = len(original_tools)
    catalog_tools_in = sum(1 for t in original_tools if t.get("name"))

    config = config or load_config()
    if for_hook:
        tools_allowed = tools_inject_allowed(config, "hook")
    else:
        tools_allowed = tools_inject_allowed(config, "proxy", upstream_kind=upstream_kind)
    if not tools_allowed:
        tokens_in = count_json_tokens(original_tools)
        return PruneResult(
            tools=original_tools,
            status="pass_through",
            query=query or None,
            tools_in=tools_in,
            mcp_tools_in=catalog_tools_in,
            tools_out=tools_in,
            error=None,
            tokens_in=tokens_in,
            tokens_out=tokens_in,
            tokens_saved=0,
            tools_accepted=copy.deepcopy(original_tools),
            tools_final=copy.deepcopy(original_tools),
        )

    configured_pipeline = (
        pruning_pipeline if pruning_pipeline is not None else pruning_pipeline_from_config(config)
    )
    terminal_stage = configured_pipeline[-1] if configured_pipeline else None
    output_policy_ctx = output_policy_context_from_config(
        config,
        terminal_stage=terminal_stage,
    )
    policy_ctx = ctx or policy_context_from_config(
        config,
        terminal_stage=terminal_stage,
    )
    if for_hook:
        prepare_hook_tool_pruning(config, policy_ctx, output_policy_ctx)
    if request_pass_through(original_tools, output_policy_ctx):
        tokens_in = count_json_tokens(original_tools)
        return PruneResult(
            tools=original_tools,
            status="pass_through",
            query=query or None,
            tools_in=tools_in,
            mcp_tools_in=catalog_tools_in,
            tools_out=tools_in,
            error=None,
            tokens_in=tokens_in,
            tokens_out=tokens_in,
            tokens_saved=0,
            tools_accepted=copy.deepcopy(original_tools),
            tools_final=copy.deepcopy(original_tools),
        )

    if not query or not original_tools:
        return PruneResult(
            tools=None,
            status="skipped",
            query=query or None,
            tools_in=tools_in,
            mcp_tools_in=catalog_tools_in,
            tools_out=None,
            error="no query or no tools",
        )

    tokens_in = count_json_tokens(original_tools)

    named_tools = [
        (tool, str(tool.get("name", "")))
        for tool in original_tools
        if isinstance(tool, dict) and str(tool.get("name", ""))
    ]
    pass_through_flags = batch_tool_pass_through(
        [name for _, name in named_tools],
        output_policy_ctx,
    )
    stashed_by_name: dict[str, dict[str, Any]] = {
        name: copy.deepcopy(tool)
        for (tool, name), passes in zip(named_tools, pass_through_flags, strict=True)
        if passes
    }

    catalog_source = tools_for_catalog(original_tools, output_policy_ctx)
    to_catalog = tools_to_catalog_entries or anthropic_tools_to_catalog_entries
    to_api = merged_to_api_tools or _merged_tools_to_anthropic
    entries, enums = to_catalog(catalog_source)
    entries = entries_for_policy(entries, output_policy_ctx)
    if not entries:
        if restored := merge_tools_preserving_order(original_tools, {}, stashed_by_name):
            tokens_out = count_json_tokens(restored)
            return PruneResult(
                tools=restored,
                status="applied",
                query=query,
                tools_in=tools_in,
                mcp_tools_in=catalog_tools_in,
                tools_out=len(restored),
                error=None,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                tokens_saved=tokens_in - tokens_out,
                tools_accepted=copy.deepcopy(original_tools),
                tools_final=copy.deepcopy(restored),
            )
        return PruneResult(
            tools=None,
            status="skipped",
            query=query,
            tools_in=tools_in,
            mcp_tools_in=catalog_tools_in,
            tools_out=None,
            error="no tools in request",
        )
    _log_tool_token_counts(tokens_in, None)

    decomposed: dict[str, int] = {}
    decomposed_breakdown: dict[str, dict[str, int]] = {}
    decomposed_catalog: dict[str, dict[str, Any]] | None = None
    pruning_token_usage: dict[str, StageTokenUsage] = {}
    pruning_model_tokens: dict[str, int] = {}
    tool_properties_count_in = 0
    tool_properties_count_out = 0
    pruning_query = tools_pruning_query(query, config, for_hook=for_hook)
    try:
        (
            merged,
            decomposed,
            decomposed_breakdown,
            decomposed_catalog,
            pruning_token_usage,
            pruning_model_tokens,
            tool_properties_count_in,
            tool_properties_count_out,
        ) = _run_catalog_pruning(
            entries,
            enums,
            pruning_query,
            configured_pipeline,
            capture_decomposed_catalog,
            policy_ctx,
            output_policy_ctx,
            skill_entries=skill_entries,
            skill_llm_out=skill_llm_out,
            config=config,
            pruner_settings=pruner_settings,
            for_hook=for_hook,
            upstream_kind=upstream_kind,
            catalog_bulk_id=catalog_bulk_id,
            phase_timer=phase_timer,
            phase_prefix=phase_prefix,
        )
    except Exception as exc:
        logger.warning("tool pruning failed: %s", exc)
        return PruneResult(
            tools=None,
            status="failed",
            query=query,
            tools_in=tools_in,
            mcp_tools_in=catalog_tools_in,
            tools_out=None,
            error=str(exc),
            tokens_in=tokens_in,
            tool_properties_count_in=tool_properties_count_in,
            tool_properties_count_out=tool_properties_count_out,
            tools_accepted=copy.deepcopy(original_tools),
            pruning_model_tokens=pruning_model_tokens,
            pruning_token_usage=pruning_token_usage,
            decomposed=decomposed,
            decomposed_breakdown=decomposed_breakdown,
            decomposed_catalog=decomposed_catalog,
        )

    if not merged:
        return PruneResult(
            tools=None,
            status="failed",
            query=query,
            tools_in=tools_in,
            mcp_tools_in=catalog_tools_in,
            tools_out=None,
            error="pruned catalog produced no tools",
            tokens_in=tokens_in,
            tool_properties_count_in=tool_properties_count_in,
            tool_properties_count_out=tool_properties_count_out,
            tools_accepted=copy.deepcopy(original_tools),
            pruning_model_tokens=pruning_model_tokens,
            pruning_token_usage=pruning_token_usage,
            decomposed=decomposed,
            decomposed_breakdown=decomposed_breakdown,
            decomposed_catalog=decomposed_catalog,
        )

    pruned_by_name = _pruned_tools_by_name(original_tools, merged, to_api)
    pruned = merge_tools_preserving_order(original_tools, pruned_by_name, stashed_by_name)
    tokens_out = count_json_tokens(pruned)
    tokens_saved = tokens_in - tokens_out
    _log_tool_token_counts(tokens_in, tokens_out)
    return PruneResult(
        tools=pruned,
        status="applied",
        query=query,
        tools_in=tools_in,
        mcp_tools_in=catalog_tools_in,
        tools_out=len(pruned),
        error=None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_saved=tokens_saved,
        tool_properties_count_in=tool_properties_count_in,
        tool_properties_count_out=tool_properties_count_out,
        tools_accepted=copy.deepcopy(original_tools),
        tools_final=copy.deepcopy(pruned),
        pruning_model_tokens=pruning_model_tokens,
        pruning_token_usage=pruning_token_usage,
        decomposed=decomposed,
        decomposed_breakdown=decomposed_breakdown,
        decomposed_catalog=decomposed_catalog,
    )

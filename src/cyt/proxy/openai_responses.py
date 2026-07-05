"""OpenAI Responses API request transform for the LLM proxy."""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cyt.skills.proxy_inject import DeferredSkillsContext

from cyt.indexer.tokens import count_json_tokens
from cyt.proxy.anthropic import (
    PruneResult,
    _text_from_user_message,
    _user_message_has_text,
    clean_messages,
    extract_last_assistant_message,
    filter_tools_for_query,
    format_search_query,
    merge_api_tool_onto_original,
)
from cyt.proxy.pruning_debug import merge_decomposed_catalog_snapshots
from cyt.pruners.policies import policy_context_from_config, request_pass_through
from cyt.pruners.remote import PrunerSettingsCache
from cyt.pruning.coordinator import CoordinateResult, ToolSource

logger = logging.getLogger(__name__)


def _openai_split_for_user_inject(
    original_tools: list[dict[str, Any]],
    pruned_named: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from cyt.proxy.user_message_inject import (
        mcp_tools_from_pruned_named,
        openai_tools_keep_system_only,
    )

    flat_original = _flatten_openai_tools_for_pruning(original_tools)
    named = pruned_named if pruned_named is not None else flat_original
    mcp_for_inject = mcp_tools_from_pruned_named(named)
    final_tools = openai_tools_keep_system_only(original_tools, named)
    return final_tools, mcp_for_inject


def _openai_input_to_messages(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI Responses ``input`` items to Anthropic-style messages for query extraction."""
    messages: list[dict[str, Any]] = []
    for item in input_items:
        if item.get("type") != "message":
            continue
        role = item.get("role")
        if role not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in ("input_text", "output_text"):
                blocks.append({"type": "text", "text": block.get("text", "")})
            else:
                blocks.append(block)
        if blocks:
            messages.append({"role": role, "content": blocks})
    return messages


def clean_input(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter OpenAI Responses input for rerank query extraction only (not forwarded upstream)."""
    return clean_messages(_openai_input_to_messages(input_items))


def extract_user_query_from_input(cleaned_messages: list[dict[str, Any]]) -> str | None:
    """Return text from the latest user message in OpenAI Responses ``input``."""
    for msg in reversed(cleaned_messages):
        if msg.get("role") != "user":
            continue
        if not _user_message_has_text(msg):
            continue
        if text := _text_from_user_message(msg):
            return text
    return None


def _openai_tool_pass_through(tool: dict[str, Any]) -> bool:
    """Native OpenAI Responses tools (e.g. tool_search, web_search) have no ``name``."""
    return isinstance(tool, dict) and not str(tool.get("name", ""))


def _openai_namespace_tool_name(namespace: str, tool_name: str) -> str:
    if namespace.startswith("mcp__") and tool_name:
        return f"{namespace}__{tool_name}"
    return tool_name or namespace


def _flatten_openai_tools_for_pruning(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand namespace tools into flat named function tools for the pruning pipeline."""
    flat: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") == "namespace":
            namespace = str(tool.get("name", ""))
            for child in tool.get("tools") or []:
                if not isinstance(child, dict) or _openai_tool_pass_through(child):
                    continue
                child_name = str(child.get("name", ""))
                flat.append(
                    {
                        **copy.deepcopy(child),
                        "name": _openai_namespace_tool_name(namespace, child_name),
                    },
                )
            continue
        if not _openai_tool_pass_through(tool):
            flat.append(tool)
    return flat


def _pruned_tools_by_name(pruned_named: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(t.get("name", "")): t for t in pruned_named if isinstance(t, dict) and t.get("name")
    }


def _merge_pruned_namespace_children(
    namespace: str,
    children: list[Any],
    pruned_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    kept_children: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        if _openai_tool_pass_through(child):
            kept_children.append(copy.deepcopy(child))
            continue
        child_name = str(child.get("name", ""))
        full_name = _openai_namespace_tool_name(namespace, child_name)
        if full_name in pruned_by_name:
            kept_children.append(merge_api_tool_onto_original(child, pruned_by_name[full_name]))
    return kept_children


def _merge_pruned_namespace_tool(
    tool: dict[str, Any],
    pruned_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    namespace = str(tool.get("name", ""))
    kept_children = _merge_pruned_namespace_children(
        namespace,
        tool.get("tools") or [],
        pruned_by_name,
    )
    if not kept_children:
        return None
    namespace_out = copy.deepcopy(tool)
    namespace_out["tools"] = kept_children
    return namespace_out


def _merge_pruned_openai_tools(
    original: list[dict[str, Any]],
    pruned_named: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rebuild OpenAI tools[] in request order, preserving structure and pass-through tools."""
    pruned_by_name = _pruned_tools_by_name(pruned_named)
    result: list[dict[str, Any]] = []
    for tool in original:
        if _openai_tool_pass_through(tool):
            result.append(copy.deepcopy(tool))
            continue
        if tool.get("type") == "namespace":
            merged_namespace = _merge_pruned_namespace_tool(tool, pruned_by_name)
            if merged_namespace is not None:
                result.append(merged_namespace)
            continue
        name = str(tool.get("name", ""))
        if name in pruned_by_name:
            result.append(merge_api_tool_onto_original(tool, pruned_by_name[name]))
    return result


def _merge_openai_tools_preserving_order(
    original: list[dict[str, Any]],
    pruned_named: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rebuild flat OpenAI tools[] in request order, preserving unnamed native tools unchanged."""
    return _merge_pruned_openai_tools(original, pruned_named)


def _merged_tools_to_openai(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in merged:
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        tool_name = tool.get("name", "")
        out.append(
            {
                "type": "function",
                "name": tool_name,
                "description": tool.get("description", ""),
                "parameters": schema,
            },
        )
    return out


def _tool_search_output_items(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in input_items
        if isinstance(item, dict) and item.get("type") == "tool_search_output" and item.get("tools")
    ]


def _mcp_tools_in(tools: list[dict[str, Any]]) -> int:
    return sum(1 for t in tools if isinstance(t, dict) and t.get("name"))


def _combine_prune_results(
    existing: PruneResult | None,
    new: PruneResult | None,
) -> PruneResult | None:
    if new is None:
        return existing
    if existing is None:
        return new

    status = existing.status
    if new.status == "applied" or existing.status == "applied":
        status = "applied"
    elif new.status == "failed":
        status = "failed"
    elif existing.status == "pass_through":
        status = new.status

    tokens_in = (existing.tokens_in or 0) + (new.tokens_in or 0)
    tokens_out = (existing.tokens_out or 0) + (new.tokens_out or 0)
    return PruneResult(
        tools=new.tools if new.tools is not None else existing.tools,
        status=status,
        query=new.query or existing.query,
        tools_in=existing.tools_in + new.tools_in,
        mcp_tools_in=existing.mcp_tools_in + new.mcp_tools_in,
        tools_out=(existing.tools_out or 0) + (new.tools_out or 0),
        error=new.error or existing.error,
        tokens_in=tokens_in or None,
        tokens_out=tokens_out or None,
        tokens_saved=tokens_in - tokens_out if tokens_in and tokens_out is not None else None,
        tool_properties_count_in=(existing.tool_properties_count_in or 0)
        + (new.tool_properties_count_in or 0),
        tool_properties_count_out=(existing.tool_properties_count_out or 0)
        + (new.tool_properties_count_out or 0),
        tools_accepted=existing.tools_accepted or new.tools_accepted,
        tools_final=new.tools_final or existing.tools_final,
        pruning_model_tokens={**existing.pruning_model_tokens, **new.pruning_model_tokens},
        pruning_token_usage={**existing.pruning_token_usage, **new.pruning_token_usage},
        decomposed={**existing.decomposed, **new.decomposed},
        decomposed_breakdown={**existing.decomposed_breakdown, **new.decomposed_breakdown},
        decomposed_catalog=merge_decomposed_catalog_snapshots(
            existing.decomposed_catalog,
            new.decomposed_catalog,
        ),
    )


def _finalize_openai_prune_result(
    tools: list[dict[str, Any]],
    result: PruneResult,
    *,
    user_message_inject: bool,
) -> tuple[list[dict[str, Any]] | None, PruneResult, list[dict[str, Any]]]:
    if result.status != "applied" or result.tools is None:
        if result.status == "failed":
            logger.warning("tool pruning failed: %s", result.error)
        if result.status != "pass_through":
            return None, result, []
        if user_message_inject:
            final_tools, mcp_for_inject = _openai_split_for_user_inject(tools)
            return final_tools, result, mcp_for_inject
        return tools, result, []

    if user_message_inject:
        final_tools, mcp_for_inject = _openai_split_for_user_inject(tools, result.tools)
    else:
        mcp_for_inject = []
        final_tools = _merge_pruned_openai_tools(tools, result.tools)

    result.tools = final_tools
    result.tools_final = copy.deepcopy(final_tools)
    result.tools_accepted = copy.deepcopy(tools)
    result.tools_in = len(tools)
    result.tools_out = len(final_tools)
    tokens_in = count_json_tokens(tools)
    tokens_out = count_json_tokens(final_tools)
    result.tokens_in = tokens_in
    result.tokens_out = tokens_out
    result.tokens_saved = tokens_in - tokens_out
    return final_tools, result, mcp_for_inject


def _prune_openai_tools_array(
    tools: list[dict[str, Any]],
    query: str | None,
    pruning_pipeline: list[str] | None,
    capture_decomposed_catalog: bool,
    *,
    skill_entries: list[Any] | None = None,
    skill_llm_out: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    user_message_inject: bool = False,
) -> tuple[list[dict[str, Any]] | None, PruneResult | None, list[dict[str, Any]]]:
    """Prune one OpenAI ``tools`` array and return updated tools plus MCP defs for user inject."""
    if not tools:
        return None, None, []

    if request_pass_through(tools, policy_context_from_config()):
        tokens_in = count_json_tokens(tools)
        mcp_for_inject: list[dict[str, Any]] = []
        final_tools: list[dict[str, Any]] | None = tools
        if user_message_inject:
            final_tools, mcp_for_inject = _openai_split_for_user_inject(tools)
        return (
            final_tools,
            PruneResult(
                tools=None,
                status="pass_through",
                query=query,
                tools_in=len(tools),
                mcp_tools_in=_mcp_tools_in(tools),
                tools_out=len(final_tools or tools),
                error=None,
                tokens_in=tokens_in,
                tokens_out=tokens_in,
                tokens_saved=0,
            ),
            mcp_for_inject,
        )

    if not query:
        return (
            None,
            PruneResult(
                tools=None,
                status="skipped",
                query=None,
                tools_in=len(tools),
                mcp_tools_in=_mcp_tools_in(tools),
                tools_out=None,
                error="no user query extracted",
            ),
            [],
        )

    named_tools = _flatten_openai_tools_for_pruning(tools)
    if not named_tools:
        return None, None, []

    result = filter_tools_for_query(
        named_tools,
        query,
        pruning_pipeline,
        capture_decomposed_catalog=capture_decomposed_catalog,
        merged_to_api_tools=_merged_tools_to_openai,
        skill_entries=skill_entries,
        skill_llm_out=skill_llm_out,
        config=config,
        pruner_settings=pruner_settings,
    )
    if result.status != "applied" or result.tools is None:
        if result.status == "failed":
            logger.warning("tool pruning failed: %s", result.error)
        if result.status != "pass_through":
            return None, result, []
        if user_message_inject:
            final_tools, mcp_for_inject = _openai_split_for_user_inject(tools)
            return final_tools, result, mcp_for_inject
        return tools, result, []

    return _finalize_openai_prune_result(
        tools,
        result,
        user_message_inject=user_message_inject,
    )


def _openai_skipped_no_query_prune_result(tools: list[dict[str, Any]]) -> PruneResult:
    return PruneResult(
        tools=None,
        status="skipped",
        query=None,
        tools_in=len(tools),
        mcp_tools_in=_mcp_tools_in(tools),
        tools_out=None,
        error="no user query extracted",
    )


def _openai_collect_source_specs(
    original: dict[str, Any],
) -> list[tuple[str, list[dict[str, Any]]]]:
    source_specs: list[tuple[str, list[dict[str, Any]]]] = []
    root_tools = original.get("tools") or []
    if root_tools:
        source_specs.append(("root", root_tools))
    for index, item in enumerate(original.get("input") or []):
        if not isinstance(item, dict) or item.get("type") != "tool_search_output":
            continue
        item_tools = item.get("tools") or []
        if item_tools:
            source_specs.append((f"tool_search_output:{index}", item_tools))
    return source_specs


def _openai_write_pruned_tools(
    original: dict[str, Any],
    source_id: str,
    final_tools: list[dict[str, Any]],
) -> None:
    if source_id == "root":
        original["tools"] = final_tools
        return
    index = int(source_id.rsplit(":", 1)[-1])
    input_items = original.get("input") or []
    if 0 <= index < len(input_items):
        item = input_items[index]
        if isinstance(item, dict):
            item["tools"] = final_tools


def _openai_prune_early_sources(
    source_specs: list[tuple[str, list[dict[str, Any]]]],
    query: str | None,
    pruning_pipeline: list[str] | None,
    *,
    capture_decomposed_catalog: bool,
    config: dict[str, Any] | None,
    pruner_settings: PrunerSettingsCache | None,
    user_message_inject: bool,
) -> tuple[PruneResult | None, list[dict[str, Any]]]:
    result: PruneResult | None = None
    mcp_for_inject: list[dict[str, Any]] = []
    for _source_id, tools in source_specs:
        stage_result: PruneResult | None
        if not query:
            stage_result = _openai_skipped_no_query_prune_result(tools)
        elif request_pass_through(tools, policy_context_from_config()):
            _final, stage_result, mcp_batch = _prune_openai_tools_array(
                tools,
                query,
                pruning_pipeline,
                capture_decomposed_catalog,
                config=config,
                pruner_settings=pruner_settings,
                user_message_inject=user_message_inject,
            )
            mcp_for_inject.extend(mcp_batch)
        else:
            continue
        result = _combine_prune_results(result, stage_result)
    return result, mcp_for_inject


def _openai_partition_tool_sources(
    source_specs: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[list[ToolSource], list[tuple[str, list[dict[str, Any]]]]]:
    tool_sources: list[ToolSource] = []
    passthrough_specs: list[tuple[str, list[dict[str, Any]]]] = []
    for source_id, tools in source_specs:
        if request_pass_through(tools, policy_context_from_config()):
            passthrough_specs.append((source_id, tools))
            continue
        named_tools = _flatten_openai_tools_for_pruning(tools)
        if not named_tools:
            continue
        tool_sources.append(
            ToolSource(
                source_id,
                named_tools,
                merged_to_api_tools=_merged_tools_to_openai,
            ),
        )
    return tool_sources, passthrough_specs


def _openai_apply_passthrough_sources(
    original: dict[str, Any],
    passthrough_specs: list[tuple[str, list[dict[str, Any]]]],
    query: str,
    pruning_pipeline: list[str] | None,
    *,
    capture_decomposed_catalog: bool,
    config: dict[str, Any] | None,
    pruner_settings: PrunerSettingsCache | None,
    user_message_inject: bool,
    result: PruneResult | None,
    mcp_for_inject: list[dict[str, Any]],
) -> PruneResult | None:
    for source_id, tools in passthrough_specs:
        final_tools, stage_result, mcp_batch = _prune_openai_tools_array(
            tools,
            query,
            pruning_pipeline,
            capture_decomposed_catalog,
            config=config,
            pruner_settings=pruner_settings,
            user_message_inject=user_message_inject,
        )
        mcp_for_inject.extend(mcp_batch)
        if final_tools is not None and stage_result is not None:
            if stage_result.status == "applied" or user_message_inject:
                _openai_write_pruned_tools(original, source_id, final_tools)
        result = _combine_prune_results(result, stage_result)
    return result


def _openai_apply_coordinated_sources(
    original: dict[str, Any],
    tool_sources: list[ToolSource],
    source_specs: list[tuple[str, list[dict[str, Any]]]],
    coordinated: CoordinateResult,
    *,
    user_message_inject: bool,
    result: PruneResult | None,
    mcp_for_inject: list[dict[str, Any]],
) -> PruneResult | None:
    for source in tool_sources:
        source_prune_result = coordinated.prune_results.get(source.source_id)
        if source_prune_result is None:
            continue
        original_tools = next(tools for sid, tools in source_specs if sid == source.source_id)
        final_tools, stage_result, mcp_batch = _finalize_openai_prune_result(
            original_tools,
            source_prune_result,
            user_message_inject=user_message_inject,
        )
        mcp_for_inject.extend(mcp_batch)
        if final_tools is not None and stage_result is not None:
            if stage_result.status == "applied" or user_message_inject:
                _openai_write_pruned_tools(original, source.source_id, final_tools)
        result = _combine_prune_results(result, stage_result)
    return result


def _openai_prune_request_tools(
    original: dict[str, Any],
    query: str | None,
    pruning_pipeline: list[str] | None,
    capture_decomposed_catalog: bool,
    deferred: DeferredSkillsContext | None,
    config: dict[str, Any] | None,
    pruner_settings: PrunerSettingsCache | None = None,
) -> tuple[PruneResult | None, list[dict[str, Any]]]:
    from cyt.config import inject_into_user_message, load_config
    from cyt.pruning.coordinator import coordinate_skills_tools_prune

    result: PruneResult | None = None
    mcp_for_inject: list[dict[str, Any]] = []
    user_message_inject = inject_into_user_message(config)
    resolved_config = config or load_config()
    skill_entries = (
        deferred.skill_entries if deferred is not None and deferred.skills_allowed else None
    )
    skill_out = deferred.skill_out if deferred is not None else {}

    source_specs = _openai_collect_source_specs(original)

    if not source_specs or not query:
        return _openai_prune_early_sources(
            source_specs,
            query,
            pruning_pipeline,
            capture_decomposed_catalog=capture_decomposed_catalog,
            config=config,
            pruner_settings=pruner_settings,
            user_message_inject=user_message_inject,
        )

    tool_sources, passthrough_specs = _openai_partition_tool_sources(source_specs)
    result = _openai_apply_passthrough_sources(
        original,
        passthrough_specs,
        query,
        pruning_pipeline,
        capture_decomposed_catalog=capture_decomposed_catalog,
        config=config,
        pruner_settings=pruner_settings,
        user_message_inject=user_message_inject,
        result=result,
        mcp_for_inject=mcp_for_inject,
    )

    if not tool_sources:
        return result, mcp_for_inject

    coordinated = coordinate_skills_tools_prune(
        query,
        resolved_config,
        tool_sources,
        skill_entries=skill_entries,
        upstream_kind="openai",
        capture_decomposed_catalog=capture_decomposed_catalog,
        pruner_settings=pruner_settings,
        skills_allowed=bool(deferred is not None and deferred.skills_allowed),
        tools_allowed=True,
        tools_pipeline_override=pruning_pipeline,
        skill_out=skill_out if deferred is not None else None,
    )
    if coordinated.skill_matches is not None and deferred is not None:
        skill_out["matches"] = coordinated.skill_matches

    result = _openai_apply_coordinated_sources(
        original,
        tool_sources,
        source_specs,
        coordinated,
        user_message_inject=user_message_inject,
        result=result,
        mcp_for_inject=mcp_for_inject,
    )
    return result, mcp_for_inject


def transform_openai_request(
    body: dict[str, Any],
    pruning_pipeline: list[str] | None = None,
    capture_decomposed_catalog: bool = False,
    config: dict[str, Any] | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
) -> tuple[dict[str, Any], PruneResult | None, Any]:
    """Return body (tools replaced when pruning applied), pruning metadata, and skills meta."""
    from cyt.skills.proxy_inject import (
        SkillsProxyInjectMeta,
        finish_deferred_skills_openai,
        prepare_deferred_skills_context,
    )

    original = copy.deepcopy(body)
    skills_meta = SkillsProxyInjectMeta()

    input_items = original.get("input") or []
    tools = original.get("tools") or []
    tool_search_outputs = _tool_search_output_items(input_items)

    cleaned = clean_input(input_items)
    user_query = extract_user_query_from_input(cleaned)
    query = (
        format_search_query(user_query, extract_last_assistant_message(cleaned))
        if user_query
        else None
    )
    deferred = prepare_deferred_skills_context(
        config,
        query,
        kind="openai",
        body=original,
    )
    skill_out = deferred.skill_out if deferred is not None else {}

    if not tools and not tool_search_outputs:
        original, skills_meta = finish_deferred_skills_openai(
            original,
            skills_meta,
            deferred,
            config,
            query=query,
            pruner_settings=pruner_settings,
        )
        return original, None, skills_meta

    if not user_query:
        logger.warning("no user query extracted; forwarding original tools")
        original, skills_meta = finish_deferred_skills_openai(
            original,
            skills_meta,
            deferred,
            config,
            pruner_settings=pruner_settings,
        )
        return original, _openai_skipped_no_query_prune_result(tools), skills_meta

    result = _openai_prune_request_tools(
        original,
        query,
        pruning_pipeline,
        capture_decomposed_catalog,
        deferred,
        config,
        pruner_settings=pruner_settings,
    )
    prune_result, mcp_for_inject = result
    original, skills_meta = finish_deferred_skills_openai(
        original,
        skills_meta,
        deferred,
        config,
        matches=skill_out.get("matches"),
        query=query,
        prune_result=prune_result,
        pruner_settings=pruner_settings,
    )

    from cyt.config import inject_into_user_message
    from cyt.proxy.user_message_inject import (
        already_has_user_turn_injection,
        append_injection_to_body,
    )
    from cyt.tools.inject import format_agent_tools

    tools_injected = False
    if inject_into_user_message(config) and mcp_for_inject:
        tools_text = format_agent_tools(mcp_for_inject)
        if tools_text and not already_has_user_turn_injection(
            original,
            "openai",
            tag="<agent-tools>",
        ):
            original = append_injection_to_body(original, tools_text, kind="openai")
            tools_injected = True

    # #region agent log
    from cyt.config import inject_via
    from cyt.proxy.agent_debug_log import agent_debug_log
    from cyt.proxy.user_message_inject import already_has_user_turn_injection as _has_inj

    agent_debug_log(
        location="openai_responses.py:transform_openai_request",
        message="proxy openai transform complete",
        hypothesis_id="C",
        data={
            "inject_via": inject_via(config),
            "inject_into_user_message": inject_into_user_message(config),
            "skills_in": getattr(skills_meta, "skills_in", 0),
            "skills_query": getattr(skills_meta, "query", None),
            "mcp_for_inject_count": len(mcp_for_inject or []),
            "tools_injected_to_user": tools_injected,
            "user_has_agent_skills": _has_inj(original, "openai", tag="<agent-skills>"),
            "user_has_agent_tools": _has_inj(original, "openai", tag="<agent-tools>"),
            "prune_status": getattr(prune_result, "status", None),
        },
    )
    # #endregion

    return original, prune_result, skills_meta

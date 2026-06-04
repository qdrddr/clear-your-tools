"""OpenAI Responses API request transform for the LLM proxy."""

from __future__ import annotations

import copy
import logging
from typing import Any

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
from cyt.pruners.policies import policy_context_from_config, request_pass_through

logger = logging.getLogger(__name__)


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
        decomposed_catalog=new.decomposed_catalog or existing.decomposed_catalog,
    )


def _prune_openai_tools_array(
    tools: list[dict[str, Any]],
    query: str | None,
    pruning_pipeline: list[str] | None,
    capture_decomposed_catalog: bool,
) -> tuple[list[dict[str, Any]] | None, PruneResult | None]:
    """Prune one OpenAI ``tools`` array (flat or namespace) and return updated tools."""
    if not tools:
        return None, None

    if request_pass_through(tools, policy_context_from_config()):
        tokens_in = count_json_tokens(tools)
        return tools, PruneResult(
            tools=None,
            status="pass_through",
            query=query,
            tools_in=len(tools),
            mcp_tools_in=_mcp_tools_in(tools),
            tools_out=len(tools),
            error=None,
            tokens_in=tokens_in,
            tokens_out=tokens_in,
            tokens_saved=0,
        )

    if not query:
        return None, PruneResult(
            tools=None,
            status="skipped",
            query=None,
            tools_in=len(tools),
            mcp_tools_in=_mcp_tools_in(tools),
            tools_out=None,
            error="no user query extracted",
        )

    named_tools = _flatten_openai_tools_for_pruning(tools)
    if not named_tools:
        return None, None

    result = filter_tools_for_query(
        named_tools,
        query,
        pruning_pipeline,
        capture_decomposed_catalog=capture_decomposed_catalog,
        merged_to_api_tools=_merged_tools_to_openai,
    )
    if result.status != "applied" or result.tools is None:
        if result.status == "failed":
            logger.warning("tool pruning failed: %s", result.error)
        if result.status != "pass_through":
            return None, result
        return tools, result

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
    return final_tools, result


def transform_openai_request(
    body: dict[str, Any],
    pruning_pipeline: list[str] | None = None,
    capture_decomposed_catalog: bool = False,
) -> tuple[dict[str, Any], PruneResult | None]:
    """Return body (tools replaced when pruning applied) and pruning metadata."""
    original = copy.deepcopy(body)
    input_items = original.get("input") or []
    tools = original.get("tools") or []
    tool_search_outputs = _tool_search_output_items(input_items)
    if not tools and not tool_search_outputs:
        return original, None

    cleaned = clean_input(input_items)
    user_query = extract_user_query_from_input(cleaned)
    if not user_query:
        logger.warning("no user query extracted; forwarding original tools")
        return original, PruneResult(
            tools=None,
            status="skipped",
            query=None,
            tools_in=len(tools),
            mcp_tools_in=_mcp_tools_in(tools),
            tools_out=None,
            error="no user query extracted",
        )

    query = format_search_query(user_query, extract_last_assistant_message(cleaned))
    result: PruneResult | None = None

    if tools:
        final_tools, pass_result = _prune_openai_tools_array(
            tools,
            query,
            pruning_pipeline,
            capture_decomposed_catalog,
        )
        if final_tools is not None and pass_result is not None and pass_result.status == "applied":
            original["tools"] = final_tools
        result = _combine_prune_results(result, pass_result)

    for item in original.get("input") or []:
        if not isinstance(item, dict) or item.get("type") != "tool_search_output":
            continue
        item_tools = item.get("tools") or []
        if not item_tools:
            continue
        final_tools, pass_result = _prune_openai_tools_array(
            item_tools,
            query,
            pruning_pipeline,
            capture_decomposed_catalog,
        )
        if final_tools is not None and pass_result is not None and pass_result.status == "applied":
            item["tools"] = final_tools
        result = _combine_prune_results(result, pass_result)

    return original, result

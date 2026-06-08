"""Anthropic API request transform for the LLM proxy."""

from __future__ import annotations

import copy
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypedDict, cast

from cyt.common.token_usage import StageTokenUsage
from cyt.config import (
    DEFAULT_PRUNING_PIPELINE,
    effective_pruning_pipeline,
    load_config,
    pruning_pipeline_from_config,
)
from cyt.indexer.build import (
    CatalogIndex,
    anthropic_tools_to_catalog_entries,
    build_catalog_index,
    catalog_tool_count,
)
from cyt.indexer.retrieve import retrieve_tools
from cyt.indexer.tokens import count_json_tokens
from cyt.pruners.bm25 import bm25_catalog_dict, prune_bm25_catalog
from cyt.pruners.llm import llm_catalog_dict, trim_catalog_dict
from cyt.pruners.policies import (
    PolicyContext,
    catalog_needs_partition,
    catalog_needs_pruned_recompose,
    drop_recomposed_tools_with_empty_properties,
    entries_for_policy,
    filter_recompose_json_entries,
    is_decomposed_optional_property_chunk,
    merge_catalog,
    merge_tools_preserving_order,
    mitigate_empty_optional_properties,
    output_policy_context_from_config,
    partition_catalog,
    policy_context_from_config,
    request_pass_through,
    tool_pass_through,
    tools_for_catalog,
)
from cyt.pruners.rerank import prune_reranked_catalog, rerank_catalog_dict

logger = logging.getLogger(__name__)

SYSTEM_REMINDER_PREFIX = "<system-reminder>"

_META_QUERY_PATTERNS = (
    re.compile(r"stepped away", re.IGNORECASE),
    re.compile(r"coming back", re.IGNORECASE),
    re.compile(r"recap in under \d+ words", re.IGNORECASE),
)

_ERROR_QUERY_PATTERNS = (
    re.compile(r"malformed and could not be parsed", re.IGNORECASE),
    re.compile(r"please retry\.?$", re.IGNORECASE),
)


@dataclass
class PruneResult:
    tools: list[dict[str, Any]] | None
    status: str
    query: str | None
    tools_in: int
    mcp_tools_in: int
    tools_out: int | None
    error: str | None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_saved: int | None = None
    tool_properties_count_in: int | None = None
    tool_properties_count_out: int | None = None
    tools_accepted: list[dict[str, Any]] | None = None
    tools_final: list[dict[str, Any]] | None = None
    pruning_model_tokens: dict[str, int] = field(default_factory=dict)
    pruning_token_usage: dict[str, StageTokenUsage] = field(default_factory=dict)
    decomposed: dict[str, int] = field(default_factory=dict)
    decomposed_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)
    decomposed_catalog: dict[str, dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "query": self.query,
            "tools_in": self.tools_in,
            "mcp_tools_in": self.mcp_tools_in,
            "tools_out": self.tools_out,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_saved": self.tokens_saved,
            "tool_properties_count_in": self.tool_properties_count_in,
            "tool_properties_count_out": self.tool_properties_count_out,
            "error": self.error,
            "decomposed": self.decomposed,
        }
        if self.decomposed_breakdown:
            out["decomposed_breakdown"] = self.decomposed_breakdown
        if self.pruning_model_tokens:
            out["pruning_model_tokens"] = self.pruning_model_tokens
        if self.pruning_token_usage:
            out["pruning_token_usage"] = {
                stage: {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "reasoning_tokens": usage.reasoning_tokens,
                    "usage_source": usage.usage_source,
                }
                for stage, usage in self.pruning_token_usage.items()
            }
        if self.decomposed_catalog is not None:
            out["decomposed_catalog"] = self.decomposed_catalog
        return out


def _is_junk_text(text: str) -> bool:
    stripped = text.strip()
    return not stripped or stripped == " " or stripped.startswith(SYSTEM_REMINDER_PREFIX)


def _clean_content_block(block: dict[str, Any]) -> dict[str, Any] | None:
    block_type = block.get("type")
    if block_type == "text":
        text = block.get("text", "")
        if not isinstance(text, str) or _is_junk_text(text):
            return None
        out: dict[str, Any] = {"type": "text", "text": text}
        citations = block.get("citations")
        if isinstance(citations, list) and citations:
            out["citations"] = citations
        return out
    if block_type == "thinking":
        thinking = block.get("thinking")
        if thinking is None:
            return None
        return {"type": "thinking", "thinking": thinking}
    if block_type == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            return None
        out = {"type": "tool_result", "content": content}
        if "is_error" in block:
            out["is_error"] = block["is_error"]
        if "tool_use_id" in block:
            out["tool_use_id"] = block["tool_use_id"]
        return out
    return None


def clean_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter messages for rerank query extraction only (not forwarded upstream)."""
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if _is_junk_text(content):
                continue
            cleaned.append({"role": msg["role"], "content": content})
            continue
        if not isinstance(content, list):
            continue
        blocks = [b for b in (_clean_content_block(x) for x in content) if b is not None]
        if not blocks:
            continue
        if len(blocks) == 1 and blocks[0].get("type") == "text":
            cleaned.append({"role": msg["role"], "content": blocks[0]["text"]})
        else:
            cleaned.append({"role": msg["role"], "content": blocks})
    return cleaned


def _user_message_has_text(msg: dict[str, Any]) -> bool:
    content = msg.get("content")
    if isinstance(content, str):
        return not _is_junk_text(content)
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "text" and not _is_junk_text(b.get("text", ""))
            for b in content
        )
    return False


def _text_from_user_message(msg: dict[str, Any]) -> str | None:
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text", "")
            if isinstance(text, str) and not _is_junk_text(text):
                parts.append(text.strip())
        if parts:
            return "\n".join(parts)
    return None


def _is_meta_user_query(text: str) -> bool:
    for pattern in _META_QUERY_PATTERNS:
        if pattern.search(text):
            return True
    if "recap" in text.lower() and "words" in text.lower() and len(text) < 200:
        return True
    return False


def _is_error_user_query(text: str) -> bool:
    for pattern in _ERROR_QUERY_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _is_junk_user_query(text: str) -> bool:
    return _is_meta_user_query(text) or _is_error_user_query(text)


def _query_rank(text: str) -> tuple[int, int]:
    """Higher sorts first: prefer real tasks over errors/recap boilerplate."""
    if _is_junk_user_query(text):
        return (-1, len(text))
    rank = 0
    if "src/" in text or ".py" in text or ".ts" in text:
        rank += 1000
    if len(text) > 80:
        rank += 500
    return (rank, len(text))


def extract_user_query(cleaned_messages: list[dict[str, Any]]) -> str | None:
    """Pick the best substantive user query, skipping recap/meta/error turns when possible."""
    candidates: list[str] = []
    for msg in reversed(cleaned_messages):
        if msg.get("role") != "user":
            continue
        if not _user_message_has_text(msg):
            continue
        if text := _text_from_user_message(msg):
            candidates.append(text)

    if not candidates:
        return None

    non_junk = [text for text in candidates if not _is_junk_user_query(text)]
    pool = non_junk if non_junk else candidates
    return max(pool, key=_query_rank)


def _text_from_assistant_message(msg: dict[str, Any]) -> str | None:
    content = msg.get("content")
    if isinstance(content, str):
        stripped = content.strip()
        return stripped if stripped and not _is_junk_text(stripped) else None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text", "")
                if isinstance(text, str) and not _is_junk_text(text):
                    parts.append(text.strip())
            elif block_type == "thinking":
                thinking = block.get("thinking", "")
                if isinstance(thinking, str) and thinking.strip():
                    parts.append(thinking.strip())
        if parts:
            return "\n".join(parts)
    return None


def extract_last_assistant_message(cleaned_messages: list[dict[str, Any]]) -> str | None:
    """Return text from the latest assistant message (text and thinking blocks)."""
    for msg in reversed(cleaned_messages):
        if msg.get("role") != "assistant":
            continue
        if text := _text_from_assistant_message(msg):
            return text
    return None


def format_search_query(user_query: str, assistant_message: str | None = None) -> str:
    """Combine user and assistant turns for bm25/rerank/llm tool search."""
    base = f"User_Asks: {user_query}"
    if assistant_message:
        return f"{base}; Assistant_Says: {assistant_message}"
    return base


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
    config = load_config()
    index = build_catalog_index(entries, enums)
    build_catalog = index.to_catalog_dict()
    data = build_catalog
    tool_properties_count_in = _count_optional_property_chunks(data)
    pipeline = effective_pruning_pipeline(
        config,
        catalog_tool_count(data),
        configured_pipeline=configured_pipeline,
    )
    terminal_stage = pipeline[-1] if pipeline else None
    reinstate_ctx = output_ctx or output_policy_context_from_config(
        config,
        terminal_stage=terminal_stage,
    )
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
    )
    tool_properties_count_out = _count_optional_property_chunks(data)
    pruning_model_tokens = _pruning_tokens_summary(pruning_token_usage)
    recompose_data = _recompose_catalog_data(
        data,
        post_rerank,
        pinned,
        catalog_index=index,
        build_catalog=build_catalog,
        post_rerank_scored=post_rerank_scored,
        pruning_pipeline=pipeline,
        ctx=ctx,
        output_ctx=reinstate_ctx,
    )
    merged = retrieve_tools(
        recompose_data,
        catalog=index,
        apply_decomposed_score_filter=False,
        ctx=reinstate_ctx,
    )
    merged = drop_recomposed_tools_with_empty_properties(merged, index, reinstate_ctx)
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
    json_items = data.get("json")
    if not isinstance(json_items, list):
        return 0
    return sum(
        1
        for item in json_items
        if isinstance(item, dict) and is_decomposed_optional_property_chunk(item)
    )


def _pruning_tokens_summary(usage_map: dict[str, StageTokenUsage]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for stage, usage in usage_map.items():
        if total := usage.input_tokens + usage.output_tokens + (usage.reasoning_tokens or 0):
            summary[stage] = total
    return summary


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
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    data, rerank_usage = rerank_catalog_dict(
        data,
        query,
        prune=False,
        merge_pinned=False,
    )
    pruning_token_usage["rerank"] = rerank_usage
    if capture_catalog and snapshots is not None:
        snapshots["rerank"] = _snapshot_catalog(data)
    post_rerank_scored = copy.deepcopy(data)
    data = prune_reranked_catalog(data)
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
    capture_catalog: bool,
    snapshots: dict[str, dict[str, Any]] | None,
    decomposed_breakdown: dict[str, dict[str, int]],
    decomposed: dict[str, int],
    pruning_token_usage: dict[str, StageTokenUsage],
) -> dict[str, Any]:
    if trim_before_llm:
        data = trim_catalog_dict(data)
    data, llm_usage = llm_catalog_dict(data, query, merge_pinned=False)
    pruning_token_usage["llm"] = llm_usage
    decomposed_breakdown["llm"] = _breakdown_entry(data)
    decomposed["llm"] = decomposed_breakdown["llm"]["json"] + decomposed_breakdown["llm"]["md"]
    if capture_catalog and snapshots is not None:
        snapshots["llm"] = _snapshot_catalog(data)
    return data


def _run_bm25_stage(
    data: dict[str, Any],
    query: str,
    *,
    capture_catalog: bool,
    snapshots: dict[str, dict[str, Any]] | None,
    decomposed_breakdown: dict[str, dict[str, int]],
    decomposed: dict[str, int],
    pruning_token_usage: dict[str, StageTokenUsage],
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    data, bm25_usage = bm25_catalog_dict(data, query, prune=False, merge_pinned=False)
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
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    stage_kwargs: _StageKwargs = {
        "data": data,
        "query": query,
        "capture_catalog": capture_catalog,
        "snapshots": snapshots,
        "decomposed_breakdown": decomposed_breakdown,
        "decomposed": decomposed,
        "pruning_token_usage": pruning_token_usage,
    }
    if stage == "rerank":
        try:
            return _run_rerank_stage(**stage_kwargs)
        except Exception as exc:
            logger.warning("rerank failed, falling back to bm25: %s", exc)
            return _run_bm25_stage(**stage_kwargs)
    if stage == "llm":
        try:
            updated = _run_llm_stage(
                **stage_kwargs,
                trim_before_llm=stage_index > 0
                and pruning_pipeline[stage_index - 1] in ("rerank", "bm25"),
            )
            return updated, None, None
        except Exception as exc:
            logger.warning("llm pruning failed, falling back to bm25: %s", exc)
            return _run_bm25_stage(**stage_kwargs)
    if stage == "bm25":
        return _run_bm25_stage(**stage_kwargs)
    raise ValueError(f"unknown pruning stage: {stage}")


def _run_pruning_pipeline(
    data: dict[str, Any],
    query: str,
    pruning_pipeline: list[str],
    capture_catalog: bool = False,
    ctx: PolicyContext | None = None,
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

    policy_ctx = ctx or policy_context_from_config()
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
        )
        if stage_post_rerank is not None:
            post_rerank = stage_post_rerank
        if stage_post_rerank_scored is not None:
            post_rerank_scored = stage_post_rerank_scored

    if pinned:
        data = merge_catalog(data, pinned)

    if pruning_token_usage:
        if parts := ", ".join(
            f"{stage}={usage.input_tokens + usage.output_tokens}"
            for stage, usage in pruning_token_usage.items()
            if usage.input_tokens or usage.output_tokens
        ):
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


def _json_entries_for_recompose(
    data: dict[str, Any],
    post_rerank: dict[str, Any] | None,
    pinned: dict[str, Any] | None,
    *,
    catalog_index: CatalogIndex,
    build_catalog: dict[str, Any] | None = None,
    post_rerank_scored: dict[str, Any] | None = None,
    pruning_pipeline: list[str] | None = None,
    ctx: PolicyContext | None = None,
    output_ctx: PolicyContext | None = None,
) -> list[dict[str, Any]]:
    """Pick json catalog entries for retrieve_tools (same inputs retrieve_catalog.py expects)."""
    policy_ctx = ctx or policy_context_from_config()
    entries: list[dict[str, Any]] = []
    seen_paths: set[object] = set()

    def _append_unique(items: object) -> None:
        if not isinstance(items, list):
            return
        for item in cast(list[object], items):
            if not isinstance(item, dict):
                continue
            chunk = cast(dict[str, Any], item)
            file_path = chunk.get("file_path")
            if file_path in seen_paths:
                continue
            seen_paths.add(file_path)
            entries.append(copy.deepcopy(chunk))

    if isinstance(pinned, dict):
        _append_unique(pinned.get("json"))
    if catalog_needs_pruned_recompose(data, policy_ctx) and post_rerank is not None:
        _append_unique(post_rerank.get("json"))
    llm_json = data.get("json") if isinstance(data.get("json"), list) else None
    _append_unique(llm_json)
    llm_selected_paths = {
        str(item.get("file_path", ""))
        for item in (llm_json or [])
        if isinstance(item, dict) and item.get("file_path")
    }

    pipeline = pruning_pipeline if pruning_pipeline is not None else DEFAULT_PRUNING_PIPELINE

    filtered = filter_recompose_json_entries(
        entries,
        ctx=policy_ctx,
        llm_selected_paths=llm_selected_paths,
    )
    mitigated = mitigate_empty_optional_properties(
        filtered,
        ctx=policy_ctx,
        catalog_index=catalog_index,
        post_rerank_scored=post_rerank_scored,
        pipeline=pipeline,
    )
    return mitigated


def _recompose_catalog_data(
    data: dict[str, Any],
    post_rerank: dict[str, Any] | None,
    pinned: dict[str, Any] | None,
    *,
    catalog_index: CatalogIndex,
    build_catalog: dict[str, Any] | None = None,
    post_rerank_scored: dict[str, Any] | None = None,
    pruning_pipeline: list[str] | None = None,
    ctx: PolicyContext | None = None,
    output_ctx: PolicyContext | None = None,
) -> dict[str, Any]:
    """Build catalog dict for retrieve_tools after pruning.

    Merges pinned roots, post-rerank json (scores), and final pipeline json, then keeps
    roots plus optional leaves that passed ``optional_leaf_survived_rerank``. Each surviving
    leaf is climbed unconditionally in ``process_groups``.
    """
    recompose: dict[str, Any] = {
        "json": _json_entries_for_recompose(
            data,
            post_rerank,
            pinned,
            catalog_index=catalog_index,
            build_catalog=build_catalog,
            post_rerank_scored=post_rerank_scored,
            pruning_pipeline=pruning_pipeline,
            ctx=ctx,
            output_ctx=output_ctx,
        ),
        "md": data.get("md", []) if isinstance(data.get("md"), list) else [],
    }
    for key in (
        "system_required_enum_values",
        "mcp_required_enum_values",
        "required_enum_values_by_tool",
    ):
        if key in data:
            recompose[key] = data[key]
        elif isinstance(pinned, dict) and key in pinned:
            recompose[key] = pinned[key]
    return recompose


def _log_operator_message(msg: str) -> None:
    """Mirror a message to the module logger, stdout, and the active debug log file."""
    from cyt.proxy.transport import append_debug_log_block, debug_endpoint_log_path

    logger.info(msg)
    print(msg, flush=True)
    if (log_path := debug_endpoint_log_path.get()) is not None:
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
) -> PruneResult:
    tools_in = len(original_tools)
    catalog_tools_in = sum(1 for t in original_tools if t.get("name"))

    config = load_config()
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

    stashed_by_name: dict[str, dict[str, Any]] = {
        name: copy.deepcopy(tool)
        for tool in original_tools
        if isinstance(tool, dict)
        and (name := str(tool.get("name", "")))
        and tool_pass_through(name, output_policy_ctx)
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
            query,
            configured_pipeline,
            capture_decomposed_catalog,
            policy_ctx,
            output_policy_ctx,
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


def transform_anthropic_request(
    body: dict[str, Any],
    pruning_pipeline: list[str] | None = None,
    capture_decomposed_catalog: bool = False,
) -> tuple[dict[str, Any], PruneResult | None]:
    """Return body (tools replaced when pruning applied) and pruning metadata."""
    original = copy.deepcopy(body)
    messages = original.get("messages") or []
    tools = original.get("tools") or []
    if not tools:
        return original, None

    if request_pass_through(tools, policy_context_from_config()):
        tokens_in = count_json_tokens(tools)
        return original, PruneResult(
            tools=None,
            status="pass_through",
            query=None,
            tools_in=len(tools),
            mcp_tools_in=sum(1 for t in tools if t.get("name")),
            tools_out=len(tools),
            error=None,
            tokens_in=tokens_in,
            tokens_out=tokens_in,
            tokens_saved=0,
        )

    cleaned = clean_messages(messages)
    user_query = extract_user_query(cleaned)
    if not user_query:
        logger.warning("no user query extracted; forwarding original tools")
        return original, PruneResult(
            tools=None,
            status="skipped",
            query=None,
            tools_in=len(tools),
            mcp_tools_in=sum(1 for t in tools if t.get("name")),
            tools_out=None,
            error="no user query extracted",
        )

    query = format_search_query(user_query, extract_last_assistant_message(cleaned))
    result = filter_tools_for_query(
        tools,
        query,
        pruning_pipeline,
        capture_decomposed_catalog=capture_decomposed_catalog,
    )
    if result.status != "applied" or result.tools is None:
        if result.status == "failed":
            logger.warning("tool pruning failed: %s", result.error)
        if result.status != "pass_through":
            return original, result

    if result.tools is not None:
        original["tools"] = result.tools
    return original, result

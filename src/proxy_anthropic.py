"""Anthropic API request transform for the LLM proxy."""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from build_index import build_catalog_index, collect_enums, count_json_tokens, prepare_tool_entry
from llm import llm_catalog_dict, trim_catalog_dict
from rerank import prune_reranked_catalog, rerank_catalog_dict
from retrieve_catalog import parse_json_input, retrieve_tools

logger = logging.getLogger(__name__)

DEFAULT_PRUNING_PIPELINE: list[str] = ["rerank"]

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
    pruning_model_tokens: dict[str, int] = field(default_factory=dict)
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
            "error": self.error,
            "decomposed": self.decomposed,
        }
        if self.decomposed_breakdown:
            out["decomposed_breakdown"] = self.decomposed_breakdown
        if self.pruning_model_tokens:
            out["pruning_model_tokens"] = self.pruning_model_tokens
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
        text = _text_from_user_message(msg)
        if text:
            candidates.append(text)

    if not candidates:
        return None

    non_junk = [text for text in candidates if not _is_junk_user_query(text)]
    pool = non_junk if non_junk else candidates
    return max(pool, key=_query_rank)


def anthropic_tools_to_catalog_entries(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Any]]:
    entries: list[dict[str, Any]] = []
    all_enums: list[Any] = []
    for tool in tools:
        name = tool.get("name", "")
        if not name:
            continue
        input_schema = tool.get("input_schema") or tool.get("inputSchema") or {}
        tool_obj = SimpleNamespace(
            name=name,
            description=tool.get("description", "") or "",
            inputSchema=copy.deepcopy(input_schema),
        )
        entry = prepare_tool_entry("", tool_obj)
        all_enums.extend(collect_enums(entry["full_schema"]["inputSchema"]))
        entries.append(entry)
    return entries, all_enums


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


def _snapshot_catalog(data: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(data)


def _run_pruning_pipeline(
    data: dict[str, Any],
    query: str,
    pruning_pipeline: list[str],
    capture_catalog: bool = False,
) -> tuple[
    dict[str, Any],
    dict[str, int],
    dict[str, dict[str, int]],
    dict[str, Any] | None,
    list[dict[str, Any]] | None,
    dict[str, dict[str, Any]] | None,
    dict[str, int],
]:
    decomposed_breakdown: dict[str, dict[str, int]] = {
        "build_index": _breakdown_entry(data),
    }
    decomposed: dict[str, int] = {
        "build_index": decomposed_breakdown["build_index"]["json"] + decomposed_breakdown["build_index"]["md"],
    }
    pruning_model_tokens: dict[str, int] = {}
    snapshots: dict[str, dict[str, Any]] | None = {} if capture_catalog else None
    post_rerank: dict[str, Any] | None = None
    pre_rerank_json: list[dict[str, Any]] | None = None

    if capture_catalog and snapshots is not None:
        snapshots["build_index"] = _snapshot_catalog(data)

    for i, stage in enumerate(pruning_pipeline):
        if stage == "rerank":
            pre_rerank_json = copy.deepcopy(data.get("json", []))
            data, rerank_tokens = rerank_catalog_dict(data, query, prune=False)
            pruning_model_tokens["rerank"] = rerank_tokens
            if capture_catalog and snapshots is not None:
                snapshots["rerank"] = _snapshot_catalog(data)
            data = prune_reranked_catalog(data)
            decomposed_breakdown["rerank"] = _breakdown_entry(data)
            decomposed["rerank"] = (
                decomposed_breakdown["rerank"]["json"] + decomposed_breakdown["rerank"]["md"]
            )
            if "llm" in pruning_pipeline[i + 1 :]:
                post_rerank = copy.deepcopy(data)
        elif stage == "llm":
            if i > 0 and pruning_pipeline[i - 1] == "rerank":
                data = trim_catalog_dict(data)
            data, llm_tokens = llm_catalog_dict(data, query)
            pruning_model_tokens["llm"] = llm_tokens
            decomposed_breakdown["llm"] = _breakdown_entry(data)
            decomposed["llm"] = decomposed_breakdown["llm"]["json"] + decomposed_breakdown["llm"]["md"]
            if capture_catalog and snapshots is not None:
                snapshots["llm"] = _snapshot_catalog(data)
        else:
            raise ValueError(f"unknown pruning stage: {stage}")

    if pruning_model_tokens:
        parts = ", ".join(
            f"{stage}={pruning_model_tokens[stage]}"
            for stage in ("rerank", "llm")
            if stage in pruning_model_tokens
        )
        if parts:
            breakdown_msg = f"pruning model tokens: {parts}"
            logger.info(breakdown_msg)
            print(breakdown_msg, flush=True)

    return data, decomposed, decomposed_breakdown, post_rerank, pre_rerank_json, snapshots, pruning_model_tokens


def _json_entries_for_recompose(
    data: dict[str, Any],
    post_rerank: dict[str, Any] | None,
    pre_rerank_json: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Pick json catalog entries for retrieve_tools (same inputs retrieve_catalog.py expects)."""
    json_paths, _ = parse_json_input(data, apply_decomposed_score_filter=False)
    if json_paths:
        json_items = data.get("json", [])
        return json_items if isinstance(json_items, list) else []

    if post_rerank is not None:
        post_json = post_rerank.get("json", [])
        if isinstance(post_json, list) and post_json:
            return post_json

    if pre_rerank_json:
        return pre_rerank_json

    return []


def _recompose_catalog_data(
    data: dict[str, Any],
    post_rerank: dict[str, Any] | None,
    pre_rerank_json: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build catalog dict for retrieve_tools after LLM.

    retrieve_catalog.reconstruct needs json file_path entries plus md scores.
    The LLM stage often keeps only md (enum) chunks and drops all json paths; rerank may
    also filter out every json chunk for weak queries. Fall back to post-rerank, then
    pre-rerank json snapshots so decomposed selections still recompile into tools.
    """
    return {
        "json": _json_entries_for_recompose(data, post_rerank, pre_rerank_json),
        "md": data.get("md", []) if isinstance(data.get("md"), list) else [],
    }


def _log_tool_token_counts(tokens_in: int, tokens_out: int | None) -> None:
    msg = f"tool tokens (compact JSON): input={tokens_in}"
    if tokens_out is not None:
        saved = tokens_in - tokens_out
        pct = (100.0 * saved / tokens_in) if tokens_in else 0.0
        msg += f", output={tokens_out}, saved={saved} ({pct:.1f}%)"
    logger.info(msg)
    print(msg, flush=True)


def filter_tools_for_query(
    original_tools: list[dict[str, Any]],
    query: str,
    pruning_pipeline: list[str] | None = None,
    capture_decomposed_catalog: bool = False,
) -> PruneResult:
    tools_in = len(original_tools)
    catalog_tools_in = sum(1 for t in original_tools if t.get("name"))

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

    entries, enums = anthropic_tools_to_catalog_entries(original_tools)
    if not entries:
        return PruneResult(
            tools=None,
            status="skipped",
            query=query,
            tools_in=tools_in,
            mcp_tools_in=catalog_tools_in,
            tools_out=None,
            error="no tools in request",
        )

    tokens_in = count_json_tokens(original_tools)
    _log_tool_token_counts(tokens_in, None)

    pipeline = pruning_pipeline if pruning_pipeline is not None else DEFAULT_PRUNING_PIPELINE
    decomposed: dict[str, int] = {}
    decomposed_breakdown: dict[str, dict[str, int]] = {}
    decomposed_catalog: dict[str, dict[str, Any]] | None = None
    pruning_model_tokens: dict[str, int] = {}
    try:
        index = build_catalog_index(entries, enums)
        data = index.to_catalog_dict()
        (
            data,
            decomposed,
            decomposed_breakdown,
            post_rerank,
            pre_rerank_json,
            decomposed_catalog,
            pruning_model_tokens,
        ) = _run_pruning_pipeline(
            data,
            query,
            pipeline,
            capture_catalog=capture_decomposed_catalog,
        )
        recompose_data = _recompose_catalog_data(data, post_rerank, pre_rerank_json)
        merged = retrieve_tools(
            recompose_data,
            catalog=index,
            apply_decomposed_score_filter=False,
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
            pruning_model_tokens=pruning_model_tokens,
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
            pruning_model_tokens=pruning_model_tokens,
            decomposed=decomposed,
            decomposed_breakdown=decomposed_breakdown,
            decomposed_catalog=decomposed_catalog,
        )

    pruned = _merged_tools_to_anthropic(merged)
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
        pruning_model_tokens=pruning_model_tokens,
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

    cleaned = clean_messages(messages)
    query = extract_user_query(cleaned)
    if not query:
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

    result = filter_tools_for_query(
        tools,
        query,
        pruning_pipeline,
        capture_decomposed_catalog=capture_decomposed_catalog,
    )
    if result.status != "applied" or result.tools is None:
        if result.status == "failed":
            logger.warning("tool pruning failed: %s", result.error)
        return original, result

    original["tools"] = result.tools
    return original, result

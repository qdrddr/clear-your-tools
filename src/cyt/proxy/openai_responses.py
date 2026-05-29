"""OpenAI Responses API request transform for the LLM proxy."""

from __future__ import annotations

import copy
import logging
from typing import Any

from cyt.indexer.build import count_json_tokens
from cyt.pruners.policies import request_pass_through

from cyt.proxy.anthropic import (
    PruneResult,
    _text_from_user_message,
    _user_message_has_text,
    clean_messages,
    filter_tools_for_query,
)

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
        text = _text_from_user_message(msg)
        if text:
            return text
    return None


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


def transform_openai_request(
    body: dict[str, Any],
    pruning_pipeline: list[str] | None = None,
    capture_decomposed_catalog: bool = False,
) -> tuple[dict[str, Any], PruneResult | None]:
    """Return body (tools replaced when pruning applied) and pruning metadata."""
    original = copy.deepcopy(body)
    input_items = original.get("input") or []
    tools = original.get("tools") or []
    if not tools:
        return original, None

    if request_pass_through(tools):
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

    cleaned = clean_input(input_items)
    query = extract_user_query_from_input(cleaned)
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
        merged_to_api_tools=_merged_tools_to_openai,
    )
    if result.status != "applied" or result.tools is None:
        if result.status == "failed":
            logger.warning("tool pruning failed: %s", result.error)
        if result.status != "pass_through":
            return original, result

    if result.tools is not None:
        original["tools"] = result.tools
    return original, result

"""Anthropic API request transform for the LLM proxy."""

from __future__ import annotations

import copy
import logging
from types import SimpleNamespace
from typing import Any

from build_index import build_catalog_index, collect_enums, prepare_tool_entry
from rerank import rerank_catalog_dict

logger = logging.getLogger(__name__)

SYSTEM_REMINDER_PREFIX = "<system-reminder>"


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


def extract_user_query(cleaned_messages: list[dict[str, Any]]) -> str | None:
    """Walk user messages in reverse; return first non-junk type:text (or string content)."""
    for msg in reversed(cleaned_messages):
        if msg.get("role") != "user":
            continue
        if not _user_message_has_text(msg):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            for block in reversed(content):
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text = block.get("text", "")
                if isinstance(text, str) and not _is_junk_text(text):
                    return text.strip()
    return None


def anthropic_tools_to_catalog_entries(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Any]]:
    entries: list[dict[str, Any]] = []
    all_enums: list[Any] = []
    for tool in tools:
        name = tool.get("name", "")
        if not name.startswith("mcp__"):
            continue
        rest = name[5:]
        parts = rest.split("__", 1)
        if len(parts) < 2:
            continue
        server_name, tool_name = parts[0], parts[1]
        input_schema = tool.get("input_schema") or tool.get("inputSchema") or {}
        tool_obj = SimpleNamespace(
            name=tool_name,
            description=tool.get("description", "") or "",
            inputSchema=copy.deepcopy(input_schema),
        )
        entry = prepare_tool_entry(server_name, tool_obj)
        all_enums.extend(collect_enums(entry["full_schema"]["inputSchema"]))
        entries.append(entry)
    return entries, all_enums


def _merged_tools_to_anthropic(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in merged:
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        out.append(
            {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "input_schema": schema,
            },
        )
    return out


def filter_tools_for_query(
    original_tools: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]] | None:
    if not query or not original_tools:
        return None
    entries, enums = anthropic_tools_to_catalog_entries(original_tools)
    if not entries:
        return None
    try:
        index = build_catalog_index(entries, enums)
        data = index.to_catalog_dict()
        data = rerank_catalog_dict(data, query)
        merged = index.pruned_tools_from_reranked(data)
    except Exception as exc:
        logger.warning("tool pruning failed: %s", exc)
        return None
    if not merged:
        return None
    return _merged_tools_to_anthropic(merged)


def transform_anthropic_request(body: dict[str, Any]) -> dict[str, Any]:
    """Return body with only tools replaced when pruning succeeds; else unchanged."""
    original = copy.deepcopy(body)
    messages = original.get("messages") or []
    tools = original.get("tools") or []
    if not tools:
        return original

    cleaned = clean_messages(messages)
    query = extract_user_query(cleaned)
    if not query:
        logger.warning("no user query extracted; forwarding original tools")
        return original

    pruned = filter_tools_for_query(tools, query)
    if pruned is None:
        return original

    original["tools"] = pruned
    return original

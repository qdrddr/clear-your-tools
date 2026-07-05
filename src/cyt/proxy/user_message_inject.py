"""Append proxy injection blocks to the latest user turn (Claude Code / Codex)."""

from __future__ import annotations

import copy
from typing import Any, Literal, cast

from cyt.pruners.policies import anthropic_tool_is_mcp, split_anthropic_tools

ProxyKind = Literal["anthropic", "openai"]

_AGENT_SKILLS_TAG = "<agent-skills>"
_AGENT_TOOLS_TAG = "<agent-tools>"


def combine_injection_parts(parts: list[str]) -> str:
    return "\n\n".join(part for part in parts if part.strip())


def split_tools_for_root_and_inject(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (mcp_tools, system_tools) from a flat Anthropic-style tool list."""
    mcp_tools, system_tools = split_anthropic_tools(tools)
    return mcp_tools, system_tools


def anthropic_tools_for_user_message_inject(
    original_tools: list[dict[str, Any]],
    pruned_tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (pruned_mcp_for_inject, original_system_for_root)."""
    mcp_tools, _ = split_tools_for_root_and_inject(pruned_tools)
    _, system_tools = split_tools_for_root_and_inject(original_tools)
    return mcp_tools, system_tools


def _message_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block_obj in content:
        if not isinstance(block_obj, dict):
            continue
        block = cast(dict[str, Any], block_obj)
        block_type = block.get("type")
        if block_type in ("input_text", "output_text", "text"):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _anthropic_last_user_message_index(messages: list[Any]) -> int | None:
    last_index: int | None = None
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            last_index = index
    return last_index


def _openai_last_user_input_index(input_items: list[Any]) -> int | None:
    last_index: int | None = None
    for index, item in enumerate(input_items):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message" and item.get("role") == "user":
            last_index = index
        elif item.get("role") == "user" and item.get("type") is None:
            last_index = index
    return last_index


def _anthropic_user_turn_text(message: dict[str, Any]) -> str:
    return _message_content_text(message.get("content"))


def _openai_user_turn_text(message: dict[str, Any]) -> str:
    return _message_content_text(message.get("content"))


def already_has_user_turn_injection(
    body: dict[str, Any],
    kind: ProxyKind,
    *,
    tag: str | None = None,
) -> bool:
    tags = [_AGENT_SKILLS_TAG, _AGENT_TOOLS_TAG] if tag is None else [tag]
    if kind == "anthropic":
        messages = body.get("messages") or []
        if not isinstance(messages, list):
            return False
        index = _anthropic_last_user_message_index(messages)
        if index is None:
            return False
        message = messages[index]
        if not isinstance(message, dict):
            return False
        text = _anthropic_user_turn_text(message)
        return any(marker in text for marker in tags)

    input_items = body.get("input") or []
    if not isinstance(input_items, list):
        return False
    index = _openai_last_user_input_index(input_items)
    if index is None:
        return False
    message = input_items[index]
    if not isinstance(message, dict):
        return False
    text = _openai_user_turn_text(message)
    return any(marker in text for marker in tags)


def _append_text_to_string_content(existing: str, text: str) -> str:
    if existing:
        return existing + "\n\n" + text
    return text


def _anthropic_append_text_to_user_content(message: dict[str, Any], text: str) -> None:
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = _append_text_to_string_content(content, text)
        return
    if not isinstance(content, list):
        message["content"] = [{"type": "text", "text": text}]
        return
    content.append({"type": "text", "text": text})


def _anthropic_insert_user_message(messages: list[dict[str, Any]], text: str) -> None:
    messages.append({"role": "user", "content": text})


def anthropic_append_to_user_turn(body: dict[str, Any], text: str) -> dict[str, Any]:
    if not text.strip():
        return body
    original = copy.deepcopy(body)
    messages = original.get("messages") or []
    if not isinstance(messages, list):
        messages = []
        original["messages"] = messages

    index = _anthropic_last_user_message_index(messages)
    if index is None:
        _anthropic_insert_user_message(messages, text)
        return original

    message = messages[index]
    if not isinstance(message, dict):
        _anthropic_insert_user_message(messages, text)
        return original

    _anthropic_append_text_to_user_content(message, text)
    return original


def _openai_make_user_message(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
    }


def openai_append_to_user_turn(body: dict[str, Any], text: str) -> dict[str, Any]:
    if not text.strip():
        return body
    original = copy.deepcopy(body)
    input_items = original.get("input") or []
    if not isinstance(input_items, list):
        input_items = []
        original["input"] = input_items

    index = _openai_last_user_input_index(input_items)
    if index is None:
        input_items.append(_openai_make_user_message(text))
        return original

    message = input_items[index]
    if not isinstance(message, dict):
        input_items.append(_openai_make_user_message(text))
        return original

    content = message.get("content")
    if not isinstance(content, list):
        message["content"] = [{"type": "input_text", "text": text}]
        return original
    content.append({"type": "input_text", "text": text})
    return original


def append_injection_to_body(body: dict[str, Any], text: str, *, kind: ProxyKind) -> dict[str, Any]:
    if kind == "anthropic":
        return anthropic_append_to_user_turn(body, text)
    return openai_append_to_user_turn(body, text)


def _openai_tool_pass_through(tool: dict[str, Any]) -> bool:
    return isinstance(tool, dict) and not str(tool.get("name", ""))


def _openai_namespace_tool_name(namespace: str, tool_name: str) -> str:
    if namespace.startswith("mcp__") and tool_name:
        return f"{namespace}__{tool_name}"
    return tool_name or namespace


def _is_system_named_tool(name: str) -> bool:
    return bool(name) and not anthropic_tool_is_mcp({"name": name})


def _merge_system_only_namespace_children(
    namespace: str,
    children: list[Any],
    pruned_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    from cyt.proxy.anthropic import merge_api_tool_onto_original

    kept_children: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        if _openai_tool_pass_through(child):
            kept_children.append(copy.deepcopy(child))
            continue
        child_name = str(child.get("name", ""))
        full_name = _openai_namespace_tool_name(namespace, child_name)
        if not _is_system_named_tool(full_name):
            continue
        if full_name in pruned_by_name:
            kept_children.append(merge_api_tool_onto_original(child, pruned_by_name[full_name]))
        else:
            kept_children.append(copy.deepcopy(child))
    return kept_children


def _merge_system_only_namespace_tool(
    tool: dict[str, Any],
    pruned_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    namespace = str(tool.get("name", ""))
    if anthropic_tool_is_mcp({"name": namespace}):
        kept_children = _merge_system_only_namespace_children(
            namespace,
            tool.get("tools") or [],
            pruned_by_name,
        )
        if not kept_children:
            return None
        namespace_out = copy.deepcopy(tool)
        namespace_out["tools"] = kept_children
        return namespace_out

    kept_children = _merge_system_only_namespace_children(
        namespace,
        tool.get("tools") or [],
        pruned_by_name,
    )
    if not kept_children:
        return None
    namespace_out = copy.deepcopy(tool)
    namespace_out["tools"] = kept_children
    return namespace_out


def openai_tools_keep_system_only(
    original: list[dict[str, Any]],
    pruned_named: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rebuild OpenAI tools[] keeping system + native pass-through tools only."""
    from cyt.proxy.anthropic import merge_api_tool_onto_original

    system_pruned = [
        tool for tool in pruned_named if _is_system_named_tool(str(tool.get("name", "")))
    ]
    pruned_by_name = {
        str(t.get("name", "")): t for t in system_pruned if isinstance(t, dict) and t.get("name")
    }
    result: list[dict[str, Any]] = []
    for tool in original:
        if _openai_tool_pass_through(tool):
            result.append(copy.deepcopy(tool))
            continue
        if tool.get("type") == "namespace":
            merged_namespace = _merge_system_only_namespace_tool(tool, pruned_by_name)
            if merged_namespace is not None:
                result.append(merged_namespace)
            continue
        name = str(tool.get("name", ""))
        if not _is_system_named_tool(name):
            continue
        if name in pruned_by_name:
            result.append(merge_api_tool_onto_original(tool, pruned_by_name[name]))
        else:
            result.append(copy.deepcopy(tool))
    return result


def mcp_tools_from_pruned_named(pruned_named: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [tool for tool in pruned_named if anthropic_tool_is_mcp(tool)]

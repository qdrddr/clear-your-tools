"""Proxy-side skills injection for Anthropic and OpenAI upstream requests."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, cast

from cyt.config import skills_enabled, skills_inject_via
from cyt.proxy.anthropic import clean_messages, extract_user_query
from cyt.proxy.openai_responses import clean_input, extract_user_query_from_input
from cyt.skills.catalog import build_registry
from cyt.skills.inject import format_agent_skills, injection_token_count
from cyt.skills.search import search_skills

_PROXY_KINDS = frozenset({"anthropic", "openai"})


@dataclass
class SkillsProxyInjectMeta:
    skills_in: int = 0
    query: str | None = None


def skills_inject_via_hook(config: dict[str, Any]) -> bool:
    return skills_enabled(config) and skills_inject_via(config) == "hook"


def skills_inject_via_proxy(config: dict[str, Any], kind: str | None) -> bool:
    if not skills_enabled(config) or skills_inject_via(config) != "proxy":
        return False
    return kind in _PROXY_KINDS


def resolve_skills_text(prompt: str, config: dict[str, Any]) -> tuple[str, int]:
    entries = build_registry(config)
    matches = search_skills(prompt, entries, config=config)
    if not matches:
        return "", 0
    injected = format_agent_skills(matches)
    if not injected:
        return "", 0
    return injected, injection_token_count(injected)


def _message_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block_obj in content:
        if not isinstance(block_obj, dict):
            continue
        block = cast("dict[str, Any]", block_obj)
        block_type = block.get("type")
        if block_type in ("input_text", "output_text", "text"):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def already_has_agent_skills(items: list[Any]) -> bool:
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str) and "<agent-skills>" in content:
            return True
        if isinstance(content, list):
            combined = _message_content_text(content)
            if "<agent-skills>" in combined:
                return True
    return False


def anthropic_find_system_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            return message
    return None


def anthropic_append_text_to_system_content(message: dict[str, Any], text: str) -> None:
    content = message.get("content")
    if isinstance(content, str):
        if content:
            message["content"] = content + "\n\n" + text
        else:
            message["content"] = text
        return
    if not isinstance(content, list):
        message["content"] = [{"type": "input_text", "text": text}]
        return
    content.append({"type": "input_text", "text": text})


def anthropic_append_skills_to_system_messages(
    messages: list[dict[str, Any]],
    text: str,
) -> list[dict[str, Any]]:
    updated = copy.deepcopy(messages)
    system = anthropic_find_system_message(updated)
    if system is None:
        updated.insert(0, {"role": "system", "content": text})
        return updated
    anthropic_append_text_to_system_content(system, text)
    return updated


def openai_make_developer_message(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": text}],
    }


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


def openai_insert_skills_developer_message(
    input_items: list[dict[str, Any]],
    text: str,
) -> list[dict[str, Any]]:
    updated = copy.deepcopy(input_items)
    developer = openai_make_developer_message(text)
    user_index = _openai_last_user_input_index(updated)
    if user_index is None:
        updated.append(developer)
    else:
        updated.insert(user_index, developer)
    return updated


def inject_skills_into_anthropic_body(
    body: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    meta = SkillsProxyInjectMeta()
    if not skills_inject_via_proxy(config, "anthropic"):
        return body, meta

    original = copy.deepcopy(body)
    messages = original.get("messages") or []
    if not isinstance(messages, list):
        return original, meta
    if already_has_agent_skills(messages):
        return original, meta

    cleaned = clean_messages(messages)
    user_query = extract_user_query(cleaned)
    if not user_query:
        return original, meta

    text, skills_in = resolve_skills_text(user_query, config)
    if skills_in <= 0:
        return original, meta

    original["messages"] = anthropic_append_skills_to_system_messages(messages, text)
    meta.skills_in = skills_in
    meta.query = user_query
    return original, meta


def inject_skills_into_openai_body(
    body: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    meta = SkillsProxyInjectMeta()
    if not skills_inject_via_proxy(config, "openai"):
        return body, meta

    original = copy.deepcopy(body)
    input_items = original.get("input") or []
    if not isinstance(input_items, list):
        return original, meta
    if already_has_agent_skills(input_items):
        return original, meta

    cleaned = clean_input(input_items)
    user_query = extract_user_query_from_input(cleaned)
    if not user_query:
        return original, meta

    text, skills_in = resolve_skills_text(user_query, config)
    if skills_in <= 0:
        return original, meta

    original["input"] = openai_insert_skills_developer_message(input_items, text)
    meta.skills_in = skills_in
    meta.query = user_query
    return original, meta

"""Proxy-side skills injection for Anthropic and OpenAI upstream requests."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, cast

from cyt.config import (
    skills_enabled,
    skills_inject_via,
    skills_pipeline_uses_deferred_proxy_inject,
)
from cyt.proxy.anthropic import (
    clean_messages,
    extract_last_assistant_message,
    extract_user_query,
    format_search_query,
)
from cyt.proxy.openai_responses import clean_input, extract_user_query_from_input
from cyt.skills.catalog import build_registry
from cyt.skills.inject import format_agent_skills, injection_token_count
from cyt.skills.search import MatchedSkill, search_skills

_PROXY_KINDS = frozenset({"anthropic", "openai"})


@dataclass
class SkillsProxyInjectMeta:
    skills_in: int = 0
    query: str | None = None
    deferred_matches: list[MatchedSkill] = field(default_factory=list)


@dataclass
class DeferredSkillsContext:
    skill_entries: list[Any] = field(default_factory=list)
    skill_out: dict[str, Any] = field(default_factory=dict)


def prepare_deferred_skills_context(
    config: dict[str, Any] | None,
    query: str | None,
    *,
    kind: str,
) -> DeferredSkillsContext | None:
    if config is None or not should_defer_skills_inject(config):
        return None
    ctx = DeferredSkillsContext()
    if query and skills_inject_via_proxy(config, kind):
        from cyt.skills.search import eligible_skills_after_gate

        ctx.skill_entries = eligible_skills_after_gate(
            query,
            build_registry(config),
            config=config,
        )
    return ctx


def finish_deferred_skills_anthropic(
    body: dict[str, Any],
    meta: SkillsProxyInjectMeta,
    deferred: DeferredSkillsContext | None,
    config: dict[str, Any] | None,
    *,
    query: str | None = None,
    matches: list[MatchedSkill] | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    if deferred is None or config is None:
        return body, meta
    return inject_skills_deferred_anthropic(
        body,
        config,
        matches=matches,
        query=query,
    )


def finish_deferred_skills_openai(
    body: dict[str, Any],
    meta: SkillsProxyInjectMeta,
    deferred: DeferredSkillsContext | None,
    config: dict[str, Any] | None,
    *,
    query: str | None = None,
    matches: list[MatchedSkill] | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    if deferred is None or config is None:
        return body, meta
    return inject_skills_deferred_openai(
        body,
        config,
        matches=matches,
        query=query,
    )


def skills_inject_via_hook(config: dict[str, Any]) -> bool:
    return skills_enabled(config) and skills_inject_via(config) == "hook"


def skills_inject_via_proxy(config: dict[str, Any], kind: str | None) -> bool:
    if not skills_enabled(config) or skills_inject_via(config) != "proxy":
        return False
    return kind in _PROXY_KINDS


def should_defer_skills_inject(config: dict[str, Any]) -> bool:
    return skills_enabled(config) and skills_pipeline_uses_deferred_proxy_inject(config)


def resolve_skills_for_query(query: str, config: dict[str, Any]) -> list[MatchedSkill]:
    entries = build_registry(config)
    return search_skills(query, entries, config=config)


def resolve_skills_text(query: str, config: dict[str, Any]) -> tuple[str, int]:
    matches = resolve_skills_for_query(query, config)
    return skills_text_from_matches(matches)


def skills_text_from_matches(matches: list[MatchedSkill]) -> tuple[str, int]:
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


def _anthropic_skills_query(messages: list[Any]) -> str | None:
    cleaned = clean_messages(messages)
    user_query = extract_user_query(cleaned)
    if not user_query:
        return None
    return format_search_query(user_query, extract_last_assistant_message(cleaned))


def _openai_skills_query(input_items: list[Any]) -> str | None:
    cleaned = clean_input(input_items)
    user_query = extract_user_query_from_input(cleaned)
    if not user_query:
        return None
    return format_search_query(user_query, extract_last_assistant_message(cleaned))


def inject_skills_matches_into_anthropic_body(
    body: dict[str, Any],
    matches: list[MatchedSkill],
    *,
    query: str | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    meta = SkillsProxyInjectMeta(query=query)
    if not matches:
        return body, meta

    original = copy.deepcopy(body)
    messages = original.get("messages") or []
    if not isinstance(messages, list):
        return original, meta
    if already_has_agent_skills(messages):
        return original, meta

    text, skills_in = skills_text_from_matches(matches)
    if skills_in <= 0:
        return original, meta

    original["messages"] = anthropic_append_skills_to_system_messages(messages, text)
    meta.skills_in = skills_in
    meta.deferred_matches = matches
    return original, meta


def inject_skills_matches_into_openai_body(
    body: dict[str, Any],
    matches: list[MatchedSkill],
    *,
    query: str | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    meta = SkillsProxyInjectMeta(query=query)
    if not matches:
        return body, meta

    original = copy.deepcopy(body)
    input_items = original.get("input") or []
    if not isinstance(input_items, list):
        return original, meta
    if already_has_agent_skills(input_items):
        return original, meta

    text, skills_in = skills_text_from_matches(matches)
    if skills_in <= 0:
        return original, meta

    original["input"] = openai_insert_skills_developer_message(input_items, text)
    meta.skills_in = skills_in
    meta.deferred_matches = matches
    return original, meta


def inject_skills_deferred_anthropic(
    body: dict[str, Any],
    config: dict[str, Any],
    *,
    matches: list[MatchedSkill] | None = None,
    query: str | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    if not skills_inject_via_proxy(config, "anthropic"):
        return body, SkillsProxyInjectMeta()

    original = copy.deepcopy(body)
    messages = original.get("messages") or []
    if not isinstance(messages, list):
        return original, SkillsProxyInjectMeta()

    resolved_query = query or _anthropic_skills_query(messages)
    if not resolved_query:
        return original, SkillsProxyInjectMeta()

    skill_matches = matches
    if skill_matches is None:
        skill_matches = resolve_skills_for_query(resolved_query, config)

    return inject_skills_matches_into_anthropic_body(
        original,
        skill_matches,
        query=resolved_query,
    )


def inject_skills_deferred_openai(
    body: dict[str, Any],
    config: dict[str, Any],
    *,
    matches: list[MatchedSkill] | None = None,
    query: str | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    if not skills_inject_via_proxy(config, "openai"):
        return body, SkillsProxyInjectMeta()

    original = copy.deepcopy(body)
    input_items = original.get("input") or []
    if not isinstance(input_items, list):
        return original, SkillsProxyInjectMeta()

    resolved_query = query or _openai_skills_query(input_items)
    if not resolved_query:
        return original, SkillsProxyInjectMeta()

    skill_matches = matches
    if skill_matches is None:
        skill_matches = resolve_skills_for_query(resolved_query, config)

    return inject_skills_matches_into_openai_body(
        original,
        skill_matches,
        query=resolved_query,
    )


def inject_skills_into_anthropic_body(
    body: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    meta = SkillsProxyInjectMeta()
    if not skills_inject_via_proxy(config, "anthropic"):
        return body, meta
    if should_defer_skills_inject(config):
        return body, meta

    original = copy.deepcopy(body)
    messages = original.get("messages") or []
    if not isinstance(messages, list):
        return original, meta
    if already_has_agent_skills(messages):
        return original, meta

    query = _anthropic_skills_query(messages)
    if not query:
        return original, meta

    text, skills_in = resolve_skills_text(query, config)
    if skills_in <= 0:
        return original, meta

    original["messages"] = anthropic_append_skills_to_system_messages(messages, text)
    meta.skills_in = skills_in
    meta.query = query
    return original, meta


def inject_skills_into_openai_body(
    body: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    meta = SkillsProxyInjectMeta()
    if not skills_inject_via_proxy(config, "openai"):
        return body, meta
    if should_defer_skills_inject(config):
        return body, meta

    original = copy.deepcopy(body)
    input_items = original.get("input") or []
    if not isinstance(input_items, list):
        return original, meta
    if already_has_agent_skills(input_items):
        return original, meta

    query = _openai_skills_query(input_items)
    if not query:
        return original, meta

    text, skills_in = resolve_skills_text(query, config)
    if skills_in <= 0:
        return original, meta

    original["input"] = openai_insert_skills_developer_message(input_items, text)
    meta.skills_in = skills_in
    meta.query = query
    return original, meta

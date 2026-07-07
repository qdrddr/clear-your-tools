"""Codex proxy skills: OpenAI Responses request-body injection."""

from __future__ import annotations

import copy
from typing import Any, cast

from cyt.config import inject_into_user_message
from cyt.indexer.tokens import count_tokens
from cyt.proxy.anthropic import PruneResult, extract_last_assistant_message, format_search_query
from cyt.proxy.openai_responses import clean_input, extract_user_query_from_input
from cyt.proxy.user_message_inject import (
    already_has_user_turn_injection,
    append_injection_to_body,
)
from cyt.pruners.remote import PrunerSettingsCache
from cyt.skills.inject import format_agent_skills
from cyt.skills.proxy_inject import DeferredSkillsContext, SkillsProxyInjectMeta
from cyt.skills.search import MatchedSkill

UPSTREAM_KIND = "openai"


def _skills_text_from_matches(matches: list[MatchedSkill]) -> tuple[str, int]:
    if not matches:
        return "", 0
    injected = format_agent_skills(matches)
    if not injected:
        return "", 0
    return injected, count_tokens(injected)


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


def proxy_skills_search_query(body: dict[str, Any]) -> str | None:
    return _openai_skills_query(body.get("input") or [])


def _openai_skills_query(input_items: list[Any]) -> str | None:
    cleaned = clean_input(input_items)
    user_query = extract_user_query_from_input(cleaned)
    if not user_query:
        return None
    return format_search_query(user_query, extract_last_assistant_message(cleaned))


def inject_skills_matches_into_openai_body(
    body: dict[str, Any],
    matches: list[MatchedSkill],
    *,
    query: str | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    meta = SkillsProxyInjectMeta(query=query)
    if not matches:
        return body, meta

    original = copy.deepcopy(body)
    input_items = original.get("input") or []
    if not isinstance(input_items, list):
        return original, meta

    use_user_turn = config is not None and inject_into_user_message(config)
    if use_user_turn:
        if already_has_user_turn_injection(original, "openai", tag="<agent-skills>"):
            return original, meta
    elif already_has_agent_skills(input_items):
        return original, meta

    text, skills_in = _skills_text_from_matches(matches)
    if skills_in <= 0:
        return original, meta

    if use_user_turn:
        original = append_injection_to_body(original, text, kind="openai")
    else:
        original["input"] = openai_insert_skills_developer_message(input_items, text)
    meta.skills_in = skills_in
    meta.skills_final_md = text
    meta.deferred_matches = matches
    return original, meta


def finish_deferred_skills_openai(
    body: dict[str, Any],
    meta: SkillsProxyInjectMeta,
    deferred: DeferredSkillsContext | None,
    config: dict[str, Any] | None,
    *,
    query: str | None = None,
    matches: list[MatchedSkill] | None = None,
    prune_result: PruneResult | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    if config is None:
        return body, meta
    from cyt.skills.proxy_inject import inject_skills_for_proxy_request, should_defer_skills_inject

    if deferred is not None and should_defer_skills_inject(config) and not deferred.skills_allowed:
        return body, meta
    return inject_skills_for_proxy_request(
        body,
        config,
        kind=UPSTREAM_KIND,
        query=query,
        matches=matches,
        prune_result=prune_result,
        pruner_settings=pruner_settings,
        deferred=deferred,
    )


def inject_skills_deferred_openai(
    body: dict[str, Any],
    config: dict[str, Any],
    *,
    matches: list[MatchedSkill] | None = None,
    query: str | None = None,
    prune_result: PruneResult | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    from cyt.skills.proxy_inject import inject_skills_for_proxy_request

    return inject_skills_for_proxy_request(
        body,
        config,
        kind=UPSTREAM_KIND,
        query=query,
        matches=matches,
        prune_result=prune_result,
    )


def inject_skills_into_openai_body(
    body: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    _ = body, config
    return body, SkillsProxyInjectMeta()

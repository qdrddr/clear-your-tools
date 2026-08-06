"""Claude proxy skills: Anthropic request-body injection."""

from __future__ import annotations

import copy
from typing import Any, Literal, cast

from cyt.config import inject_into_user_message
from cyt.indexer.tokens import count_tokens
from cyt.injection.pre_exposed import filter_pre_exposed_skills
from cyt.injection.session_text import session_text_from_proxy_body
from cyt.proxy.anthropic import (
    PruneResult,
    clean_messages,
    extract_last_assistant_message,
    extract_user_query,
    format_search_query,
)
from cyt.proxy.user_message_inject import (
    already_has_user_turn_injection,
    append_injection_to_body,
)
from cyt.pruners.remote import PrunerSettingsCache
from cyt.skills.executor_skill import with_executor_skill_matches
from cyt.skills.inject import format_agent_skills
from cyt.skills.proxy_inject import DeferredSkillsContext, SkillsProxyInjectMeta
from cyt.skills.search import MatchedSkill

UPSTREAM_KIND: Literal["anthropic"] = "anthropic"


def _skills_text_from_matches(
    matches: list[MatchedSkill],
    body: dict[str, Any],
) -> tuple[str, int]:
    if not matches:
        return "", 0
    session_text = session_text_from_proxy_body(body, UPSTREAM_KIND)
    gated = filter_pre_exposed_skills(matches, session_text)
    injected = format_agent_skills(gated)
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
        message["content"] = [{"type": "text", "text": text}]
        return
    content.append({"type": "text", "text": text})


def _anthropic_system_value_text(system: object) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return _message_content_text(system)
    return ""


def already_has_agent_skills_in_anthropic(body: dict[str, Any]) -> bool:
    messages = body.get("messages") or []
    if isinstance(messages, list) and already_has_agent_skills(messages):
        return True
    system = body.get("system")
    if system is not None:
        return "<agent-skills>" in _anthropic_system_value_text(system)
    return False


def anthropic_append_text_to_system_value(system: object, text: str) -> str | list[Any]:
    if isinstance(system, str):
        return system + "\n\n" + text if system else text
    if isinstance(system, list):
        updated: list[Any] = copy.deepcopy(system)
        updated.append({"type": "text", "text": text})
        return updated
    return text


def anthropic_append_skills_to_body(body: dict[str, Any], text: str) -> dict[str, Any]:
    original = copy.deepcopy(body)
    if original.get("system") is not None:
        original["system"] = anthropic_append_text_to_system_value(original["system"], text)
        return original
    messages = original.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    original["messages"] = anthropic_append_skills_to_system_messages(messages, text)
    return original


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


def proxy_skills_search_query(body: dict[str, Any]) -> str | None:
    return _anthropic_skills_query(body.get("messages") or [])


def _anthropic_skills_query(messages: list[Any]) -> str | None:
    cleaned = clean_messages(messages)
    user_query = extract_user_query(cleaned)
    if not user_query:
        return None
    return format_search_query(user_query, extract_last_assistant_message(cleaned))


def inject_skills_matches_into_anthropic_body(
    body: dict[str, Any],
    matches: list[MatchedSkill],
    *,
    query: str | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    meta = SkillsProxyInjectMeta(query=query)
    resolved_matches = (
        with_executor_skill_matches(list(matches), config) if config is not None else list(matches)
    )
    if not resolved_matches:
        return body, meta

    original = copy.deepcopy(body)
    messages = original.get("messages") or []
    if not isinstance(messages, list):
        return original, meta

    use_user_turn = config is not None and inject_into_user_message(config, agent="claude")
    if use_user_turn:
        if already_has_user_turn_injection(original, "anthropic", tag="<agent-skills>"):
            return original, meta
    elif already_has_agent_skills_in_anthropic(original):
        return original, meta

    text, skills_in = _skills_text_from_matches(resolved_matches, original)
    if skills_in <= 0:
        return original, meta

    if use_user_turn:
        original = append_injection_to_body(original, text, kind="anthropic")
    else:
        original = anthropic_append_skills_to_body(original, text)

    meta.skills_in = skills_in
    meta.skills_final_md = text
    meta.deferred_matches = resolved_matches
    return original, meta


def finish_deferred_skills_anthropic(
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
    from cyt.skills.proxy_inject import should_defer_skills_inject

    if deferred is not None and should_defer_skills_inject(config) and not deferred.skills_allowed:
        return body, meta
    from cyt.skills.proxy_inject import inject_skills_for_proxy_request

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


def inject_skills_deferred_anthropic(
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


def inject_skills_into_anthropic_body(
    body: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    _ = body, config
    return body, SkillsProxyInjectMeta()

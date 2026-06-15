"""Proxy-side skills injection for Anthropic and OpenAI upstream requests."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, cast

from cyt.config import (
    skills_pipeline_uses_deferred_proxy_inject,
)
from cyt.proxy.anthropic import (
    PruneResult,
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
    request_tokens: int = 0
    skills_final_md: str | None = None
    deferred_matches: list[MatchedSkill] = field(default_factory=list)


@dataclass
class DeferredSkillsContext:
    skill_entries: list[Any] = field(default_factory=list)
    skill_out: dict[str, Any] = field(default_factory=dict)
    skills_allowed: bool = False
    pre_pruner_effective_max: int = 0


def skills_inject_via_hook(config: dict[str, Any]) -> bool:
    from cyt.skills.budget import skills_inject_allowed

    return skills_inject_allowed(config, "hook")


def skills_inject_via_proxy(config: dict[str, Any], kind: str | None) -> bool:
    from cyt.skills.budget import skills_inject_allowed

    if not skills_inject_allowed(config, "proxy"):
        return False
    return kind in _PROXY_KINDS


def prepare_deferred_skills_context(
    config: dict[str, Any] | None,
    query: str | None,
    *,
    kind: str,
    body: dict[str, Any] | None = None,
) -> DeferredSkillsContext | None:
    if config is None or not should_defer_skills_inject(config):
        return None
    if not skills_inject_via_proxy(config, kind):
        return None

    ctx = DeferredSkillsContext()
    if not query:
        return ctx

    from cyt.skills.budget import proxy_pre_pruner_budget_allows, resolve_inject_budget

    if body is not None and not proxy_pre_pruner_budget_allows(config, body, kind=kind):
        return ctx

    ctx.skills_allowed = True
    if body is not None:
        from cyt.skills.budget import count_upstream_body_tokens

        total = count_upstream_body_tokens(body, kind=kind)
        pre_budget = resolve_inject_budget(
            config,
            "proxy",
            total_request_tokens=total,
            savings_tokens=0,
        )
        ctx.pre_pruner_effective_max = pre_budget.effective_max

    from cyt.skills.search import eligible_skills_after_gate

    ctx.skill_entries = eligible_skills_after_gate(
        query,
        build_registry(config, upstream_kind=kind),
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
    prune_result: PruneResult | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    if config is None:
        return body, meta
    if deferred is not None and should_defer_skills_inject(config) and not deferred.skills_allowed:
        return body, meta
    return inject_skills_for_proxy_request(
        body,
        config,
        kind="anthropic",
        query=query,
        matches=matches,
        prune_result=prune_result,
    )


def finish_deferred_skills_openai(
    body: dict[str, Any],
    meta: SkillsProxyInjectMeta,
    deferred: DeferredSkillsContext | None,
    config: dict[str, Any] | None,
    *,
    query: str | None = None,
    matches: list[MatchedSkill] | None = None,
    prune_result: PruneResult | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    if config is None:
        return body, meta
    if deferred is not None and should_defer_skills_inject(config) and not deferred.skills_allowed:
        return body, meta
    return inject_skills_for_proxy_request(
        body,
        config,
        kind="openai",
        query=query,
        matches=matches,
        prune_result=prune_result,
    )


def should_defer_skills_inject(config: dict[str, Any]) -> bool:
    from cyt.config import skills_enabled

    return skills_enabled(config) and skills_pipeline_uses_deferred_proxy_inject(config)


def resolve_skills_for_query(
    query: str,
    config: dict[str, Any],
    *,
    max_tokens: int | None = None,
    upstream_kind: str | None = None,
) -> list[MatchedSkill]:
    entries = build_registry(config, upstream_kind=upstream_kind)
    return search_skills(query, entries, config=config, max_tokens=max_tokens)


def inject_skills_for_proxy_request(
    body: dict[str, Any],
    config: dict[str, Any],
    *,
    kind: str,
    query: str | None = None,
    matches: list[MatchedSkill] | None = None,
    prune_result: PruneResult | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    if not skills_inject_via_proxy(config, kind):
        return body, SkillsProxyInjectMeta()

    original = copy.deepcopy(body)
    resolved_query = query or proxy_skills_search_query(original, kind=kind)
    if kind == "anthropic":
        inject_fn = inject_skills_matches_into_anthropic_body
    else:
        inject_fn = inject_skills_matches_into_openai_body

    if not resolved_query:
        return original, SkillsProxyInjectMeta()

    from cyt.skills.budget import (
        count_proxy_skills_request_tokens,
        count_upstream_body_tokens,
        resolve_inject_budget,
    )

    stats_request_tokens = count_proxy_skills_request_tokens(original, kind=kind)
    total = count_upstream_body_tokens(original, kind=kind)
    savings_tokens = 0
    if prune_result is not None:
        savings_tokens = int(getattr(prune_result, "tokens_saved", 0) or 0)
    savings_rate = (savings_tokens / total) if total > 0 else 0.0

    budget = resolve_inject_budget(
        config,
        "proxy",
        total_request_tokens=total,
        savings_tokens=savings_tokens,
        savings_rate=savings_rate,
    )
    if budget.effective_max <= 0:
        return original, SkillsProxyInjectMeta(
            query=resolved_query,
            request_tokens=stats_request_tokens,
        )

    skill_matches = matches
    if skill_matches is None:
        skill_matches = resolve_skills_for_query(
            resolved_query,
            config,
            max_tokens=budget.effective_max,
            upstream_kind=kind,
        )
    else:
        from cyt.skills.select import select_skills_within_budget

        skill_matches = select_skills_within_budget(skill_matches, budget.effective_max)

    body_out, meta = inject_fn(original, skill_matches, query=resolved_query)
    meta.request_tokens = stats_request_tokens
    return body_out, meta


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
        message["content"] = [{"type": "text", "text": text}]
        return
    content.append({"type": "text", "text": text})


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


def proxy_skills_search_query(body: dict[str, Any], *, kind: str) -> str | None:
    """Build format_search_query(user, assistant) from an upstream proxy body."""
    if kind == "anthropic":
        return _anthropic_skills_query(body.get("messages") or [])
    if kind == "openai":
        return _openai_skills_query(body.get("input") or [])
    return None


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
    meta.skills_final_md = text
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
    meta.skills_final_md = text
    meta.deferred_matches = matches
    return original, meta


def inject_skills_deferred_anthropic(
    body: dict[str, Any],
    config: dict[str, Any],
    *,
    matches: list[MatchedSkill] | None = None,
    query: str | None = None,
    prune_result: PruneResult | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    return inject_skills_for_proxy_request(
        body,
        config,
        kind="anthropic",
        query=query,
        matches=matches,
        prune_result=prune_result,
    )


def inject_skills_deferred_openai(
    body: dict[str, Any],
    config: dict[str, Any],
    *,
    matches: list[MatchedSkill] | None = None,
    query: str | None = None,
    prune_result: PruneResult | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    return inject_skills_for_proxy_request(
        body,
        config,
        kind="openai",
        query=query,
        matches=matches,
        prune_result=prune_result,
    )


def inject_skills_into_anthropic_body(
    body: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    """Legacy entry — injection is deferred until after tool pruning."""
    _ = body, config
    return body, SkillsProxyInjectMeta()


def inject_skills_into_openai_body(
    body: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    """Legacy entry — injection is deferred until after tool pruning."""
    _ = body, config
    return body, SkillsProxyInjectMeta()

"""Codex proxy skills: OpenAI Responses request-body injection."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from cyt.injection.pre_exposure_context import PreExposureContext

from cyt.config import inject_into_user_message
from cyt.indexer.tokens import count_tokens
from cyt.injection.block_merge import merge_injection_into_text
from cyt.injection.pre_exposed import filter_pre_exposed_skills
from cyt.injection.session_text import session_text_from_proxy_body
from cyt.proxy.anthropic import PruneResult, extract_last_assistant_message, format_search_query
from cyt.proxy.openai_responses import clean_input, extract_user_query_from_input
from cyt.proxy.user_message_inject import append_injection_to_body, prepare_agent_skills_inject_body
from cyt.pruners.remote import PrunerSettingsCache
from cyt.skills.executor_skill import with_executor_skill_matches
from cyt.skills.inject import format_agent_skills
from cyt.skills.proxy_inject import DeferredSkillsContext, SkillsProxyInjectMeta
from cyt.skills.search import MatchedSkill

UPSTREAM_KIND: Literal["openai"] = "openai"


def _skills_text_from_matches(
    matches: list[MatchedSkill],
    body: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    pre_exposure_ctx: PreExposureContext | None = None,
) -> tuple[str, int]:
    if not matches:
        return "", 0
    combined_text = ""
    if pre_exposure_ctx is not None and config is not None:
        from cyt.injection.pre_exposure_pipeline import gate_and_filter_skills

        gated, _logs = gate_and_filter_skills(
            matches,
            config=config,
            ctx=pre_exposure_ctx,
        )
        combined_text = pre_exposure_ctx.combined_text
    else:
        session_text = session_text_from_proxy_body(body, UPSTREAM_KIND)
        gated = filter_pre_exposed_skills(matches, session_text)
    injected = format_agent_skills(gated, combined_text=combined_text)
    if not injected:
        return "", 0
    return injected, count_tokens(injected)


def _merge_into_developer_text_blocks(blocks: list[Any], text: str) -> bool:
    for block_obj in blocks:
        if not isinstance(block_obj, dict):
            continue
        block = cast(dict[str, Any], block_obj)
        if block.get("type") != "input_text":
            continue
        block_text = block.get("text")
        if not isinstance(block_text, str) or "<agent-skills" not in block_text:
            continue
        block["text"] = merge_injection_into_text(block_text, text, same_turn=True)
        return True
    return False


def _find_developer_message_with_skills(input_items: list[Any]) -> dict[str, Any] | None:
    for item in input_items:
        if not isinstance(item, dict) or item.get("role") != "developer":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block_obj in content:
            if not isinstance(block_obj, dict):
                continue
            block_text = block_obj.get("text")
            if isinstance(block_text, str) and "<agent-skills" in block_text:
                return item
    return None


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
    *,
    same_turn: bool = False,
) -> list[dict[str, Any]]:
    if not text.strip():
        return input_items
    updated = copy.deepcopy(input_items)
    if same_turn:
        existing = _find_developer_message_with_skills(updated)
        if existing is not None:
            content = existing.get("content")
            if isinstance(content, list) and _merge_into_developer_text_blocks(content, text):
                return updated
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
    pre_exposure_ctx: PreExposureContext | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    meta = SkillsProxyInjectMeta(query=query)
    resolved_matches = (
        with_executor_skill_matches(list(matches), config) if config is not None else list(matches)
    )
    if not resolved_matches:
        return body, meta

    original = copy.deepcopy(body)
    input_items = original.get("input") or []
    if not isinstance(input_items, list):
        return original, meta

    use_user_turn = config is not None and inject_into_user_message(config, agent="codex")
    original, same_turn = prepare_agent_skills_inject_body(
        original,
        kind=UPSTREAM_KIND,
        use_user_turn=use_user_turn,
    )

    text, skills_in = _skills_text_from_matches(
        resolved_matches,
        original,
        config=config,
        pre_exposure_ctx=pre_exposure_ctx,
    )
    if skills_in <= 0:
        return original, meta

    if use_user_turn:
        original = append_injection_to_body(
            original,
            text,
            kind="openai",
            same_turn=same_turn,
        )
    else:
        input_items = original.get("input") or []
        if not isinstance(input_items, list):
            input_items = []
        original["input"] = openai_insert_skills_developer_message(
            input_items,
            text,
            same_turn=same_turn,
        )
    meta.skills_in = skills_in
    meta.skills_final_md = text
    meta.deferred_matches = resolved_matches
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
    pre_exposure_ctx: PreExposureContext | None = None,
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
        pre_exposure_ctx=pre_exposure_ctx,
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

"""Proxy-side skills injection orchestrator (delegates to agent skills_proxy)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from cyt.agents._types import AgentName
from cyt.proxy.anthropic import PruneResult
from cyt.pruners.remote import PrunerSettingsCache
from cyt.skills.catalog import build_registry
from cyt.skills.inject import format_agent_skills
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
    from cyt.config import skills_enabled

    if config is None or not skills_enabled(config):
        return None
    if not skills_inject_via_proxy(config, kind):
        return None

    ctx = DeferredSkillsContext()
    if not query:
        return ctx

    from cyt.skills.budget import proxy_pre_pruner_budget_allows, resolve_inject_budget

    budget_allows = body is None or proxy_pre_pruner_budget_allows(config, body, kind=kind)
    if not budget_allows:
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


def _agent_for_proxy_kind(kind: str) -> AgentName:
    if kind == "anthropic":
        return "claude"
    if kind == "openai":
        return "codex"
    raise ValueError(f"unsupported proxy kind: {kind}")


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
    from cyt.agents.claude.skills_proxy import finish_deferred_skills_anthropic as finish

    return finish(
        body,
        meta,
        deferred,
        config,
        query=query,
        matches=matches,
        prune_result=prune_result,
        pruner_settings=pruner_settings,
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
    pruner_settings: PrunerSettingsCache | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    from cyt.agents.codex.skills_proxy import finish_deferred_skills_openai as finish

    return finish(
        body,
        meta,
        deferred,
        config,
        query=query,
        matches=matches,
        prune_result=prune_result,
        pruner_settings=pruner_settings,
    )


def should_defer_skills_inject(config: dict[str, Any]) -> bool:
    from cyt.config import skills_enabled

    return skills_enabled(config) and skills_inject_via_proxy(config, "anthropic")


def resolve_skills_for_query(
    query: str,
    config: dict[str, Any],
    *,
    max_tokens: int | None = None,
    upstream_kind: str | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    entries: list[Any] | None = None,
    skip_frontmatter_gate: bool = False,
) -> list[MatchedSkill]:
    resolved_entries = (
        entries if entries is not None else build_registry(config, upstream_kind=upstream_kind)
    )
    return search_skills(
        query,
        resolved_entries,
        config=config,
        max_tokens=max_tokens,
        pruner_settings=pruner_settings,
        skip_frontmatter_gate=skip_frontmatter_gate,
    )


def inject_skills_for_proxy_request(
    body: dict[str, Any],
    config: dict[str, Any],
    *,
    kind: str,
    query: str | None = None,
    matches: list[MatchedSkill] | None = None,
    prune_result: PruneResult | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    deferred: DeferredSkillsContext | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    if not skills_inject_via_proxy(config, kind):
        return body, SkillsProxyInjectMeta()

    original = copy.deepcopy(body)
    resolved_query = query or proxy_skills_search_query(original, kind=kind)
    from cyt.agents._registry import get_agent

    cap = get_agent(_agent_for_proxy_kind(kind)).skills_proxy
    assert cap is not None
    inject_fn = cap.inject_matches_into_body

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
        reuse_entries = (
            deferred is not None and deferred.skills_allowed and bool(deferred.skill_entries)
        )
        skill_matches = resolve_skills_for_query(
            resolved_query,
            config,
            max_tokens=budget.effective_max,
            upstream_kind=kind,
            pruner_settings=pruner_settings,
            entries=(deferred.skill_entries if reuse_entries and deferred is not None else None),
            skip_frontmatter_gate=reuse_entries,
        )
    else:
        from cyt.skills.select import select_skills_within_budget

        skill_matches = select_skills_within_budget(skill_matches, budget.effective_max)

    body_out, meta = inject_fn(
        original,
        skill_matches,
        query=resolved_query,
        config=config,
    )
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
    from cyt.indexer.tokens import count_tokens

    return injected, count_tokens(injected)


def proxy_skills_search_query(body: dict[str, Any], *, kind: str) -> str | None:
    from cyt.agents._registry import get_agent

    cap = get_agent(_agent_for_proxy_kind(kind)).skills_proxy
    if cap is None:
        return None
    return cap.skills_search_query(body)


def inject_skills_matches_into_anthropic_body(
    body: dict[str, Any],
    matches: list[MatchedSkill],
    *,
    query: str | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    from cyt.agents.claude.skills_proxy import inject_skills_matches_into_anthropic_body as inject

    return inject(body, matches, query=query, config=config)


def inject_skills_matches_into_openai_body(
    body: dict[str, Any],
    matches: list[MatchedSkill],
    *,
    query: str | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    from cyt.agents.codex.skills_proxy import inject_skills_matches_into_openai_body as inject

    return inject(body, matches, query=query, config=config)


def inject_skills_deferred_anthropic(
    body: dict[str, Any],
    config: dict[str, Any],
    *,
    matches: list[MatchedSkill] | None = None,
    query: str | None = None,
    prune_result: PruneResult | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    from cyt.agents.claude.skills_proxy import inject_skills_deferred_anthropic as inject

    return inject(body, config, matches=matches, query=query, prune_result=prune_result)


def inject_skills_deferred_openai(
    body: dict[str, Any],
    config: dict[str, Any],
    *,
    matches: list[MatchedSkill] | None = None,
    query: str | None = None,
    prune_result: PruneResult | None = None,
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    from cyt.agents.codex.skills_proxy import inject_skills_deferred_openai as inject

    return inject(body, config, matches=matches, query=query, prune_result=prune_result)


def inject_skills_into_anthropic_body(
    body: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    from cyt.agents.claude.skills_proxy import inject_skills_into_anthropic_body as inject

    return inject(body, config)


def inject_skills_into_openai_body(
    body: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], SkillsProxyInjectMeta]:
    from cyt.agents.codex.skills_proxy import inject_skills_into_openai_body as inject

    return inject(body, config)


__all__ = [
    "DeferredSkillsContext",
    "SkillsProxyInjectMeta",
    "finish_deferred_skills_anthropic",
    "finish_deferred_skills_openai",
    "inject_skills_deferred_anthropic",
    "inject_skills_deferred_openai",
    "inject_skills_for_proxy_request",
    "inject_skills_into_anthropic_body",
    "inject_skills_into_openai_body",
    "inject_skills_matches_into_anthropic_body",
    "inject_skills_matches_into_openai_body",
    "prepare_deferred_skills_context",
    "proxy_skills_search_query",
    "resolve_skills_for_query",
    "resolve_skills_text",
    "should_defer_skills_inject",
    "skills_inject_via_hook",
    "skills_inject_via_proxy",
    "skills_text_from_matches",
]

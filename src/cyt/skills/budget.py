"""Skills injection budget: per-request math, global caps, token counting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from cyt.config import (
    skills_bm25_node_fallback_threshold,
    skills_enabled,
    skills_hook_inject_cap_multiplier,
    skills_hook_request_budget_fraction,
    skills_inject_via,
    skills_max_tokens_per_request,
    skills_proxy_inject_cap_fraction,
    skills_proxy_request_budget_fraction,
    skills_proxy_savings_budget_fraction,
    skills_proxy_savings_rate_threshold,
    stats_db_path,
)
from cyt.indexer.tokens import count_json_tokens, count_tokens

InjectPath = Literal["hook", "proxy"]


@dataclass(frozen=True)
class SkillsGlobalState:
    skills_injected_total: int
    # skills_in + tools_sent_upstream; excludes savings and llm/rerank pipeline costs
    cumulative_request_tokens: int
    prior_net_savings_tokens: int
    limit_global: int
    limit_global_remaining: int
    bootstrap: bool


@dataclass
class SkillsInjectBudget:
    inject_path: InjectPath
    total_request_tokens: int
    per_request_budget: int
    effective_max: int
    limit_global_remaining: int
    bootstrap: bool
    savings_tokens: int = 0
    savings_rate: float = 0.0
    debug: dict[str, int] = field(default_factory=dict)


def skills_inject_allowed(
    config: dict[str, Any],
    inject_path: InjectPath,
    *,
    cli_prompt: bool = False,
) -> bool:
    if not skills_enabled(config) and not cli_prompt:
        return False
    return skills_inject_via(config) == inject_path


def skills_budget_precheck(config: dict[str, Any] | None = None) -> bool:
    return skills_max_tokens_per_request(config) > 0


def count_skills_search_query_tokens(query: str | None) -> int:
    """Token count for user prompt + last assistant search text."""
    if not query:
        return 0
    return count_tokens(query)


def count_hook_request_tokens(payload: dict[str, Any]) -> int:
    from cyt.skills.transcript import skills_search_query_from_hook_payload

    return count_skills_search_query_tokens(skills_search_query_from_hook_payload(payload))


def count_proxy_skills_request_tokens(body: dict[str, Any], *, kind: str) -> int:
    """Token count for user prompt + last assistant from an upstream proxy body."""
    from cyt.skills.proxy_inject import proxy_skills_search_query

    return count_skills_search_query_tokens(proxy_skills_search_query(body, kind=kind))


def count_upstream_body_tokens(body: dict[str, Any], *, kind: str) -> int:
    """Count all upstream input tokens: system + messages + tools."""
    total = 0
    if kind == "anthropic":
        messages = body.get("messages")
        if isinstance(messages, list):
            total += count_json_tokens(messages)
        tools = body.get("tools")
        if isinstance(tools, list) and tools:
            total += count_json_tokens(tools)
        system = body.get("system")
        if system is not None:
            total += count_json_tokens(system)
        return total

    if kind == "openai":
        input_items = body.get("input")
        if isinstance(input_items, list):
            total += count_json_tokens(input_items)
        tools = body.get("tools")
        if isinstance(tools, list) and tools:
            total += count_json_tokens(tools)
        instructions = body.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            total += count_tokens(instructions)
        return total

    return count_json_tokens(body)


def compute_per_request_budget(
    config: dict[str, Any],
    inject_path: InjectPath,
    *,
    total_request_tokens: int,
    savings_tokens: int = 0,
    savings_rate: float | None = None,
) -> tuple[int, dict[str, int]]:
    """Fraction math + max_tokens cap; no DB."""
    debug: dict[str, int] = {}
    max_tokens = skills_max_tokens_per_request(config)
    if total_request_tokens <= 0:
        return 0, debug

    if inject_path == "hook":
        fraction = skills_hook_request_budget_fraction(config)
        per_request = int(total_request_tokens * fraction)
        debug["limit_request"] = per_request
        return min(per_request, max_tokens), debug

    rate = savings_rate
    if rate is None and total_request_tokens > 0:
        rate = savings_tokens / total_request_tokens
    rate = rate or 0.0

    limit_savings = int(savings_tokens * skills_proxy_savings_budget_fraction(config))
    limit_request = int(total_request_tokens * skills_proxy_request_budget_fraction(config))
    threshold = skills_proxy_savings_rate_threshold(config)
    limit_marginal = 0
    if rate > threshold and savings_tokens > 0:
        limit_marginal = int(savings_tokens * (rate - threshold) / rate)

    debug["limit_savings"] = limit_savings
    debug["limit_request"] = limit_request
    debug["limit_marginal"] = limit_marginal
    per_request = max(limit_savings, limit_request, limit_marginal)
    debug["per_request"] = per_request
    return min(per_request, max_tokens), debug


def apply_global_budget_cap(
    config: dict[str, Any],
    inject_path: InjectPath,
    effective_max: int,
    *,
    budget_state: SkillsGlobalState | None = None,
    total_request_tokens: int = 0,
    savings_tokens: int = 0,
    savings_rate: float = 0.0,
    debug: dict[str, int] | None = None,
) -> SkillsInjectBudget:
    if effective_max <= 0:
        return SkillsInjectBudget(
            inject_path=inject_path,
            total_request_tokens=total_request_tokens,
            per_request_budget=0,
            effective_max=0,
            limit_global_remaining=0,
            bootstrap=True,
            savings_tokens=savings_tokens,
            savings_rate=savings_rate,
            debug=debug or {},
        )

    state = budget_state
    if state is None:
        from cyt.proxy.stats import StatsDB

        db = StatsDB.open(stats_db_path(config))
        try:
            state = db.query_skills_budget_state(config, inject_path)
        finally:
            db.close()

    remaining = state.limit_global_remaining
    if not state.bootstrap:
        effective_max = min(effective_max, remaining)

    per_request = (debug or {}).get("per_request", effective_max)
    return SkillsInjectBudget(
        inject_path=inject_path,
        total_request_tokens=total_request_tokens,
        per_request_budget=per_request,
        effective_max=max(0, effective_max),
        limit_global_remaining=remaining,
        bootstrap=state.bootstrap,
        savings_tokens=savings_tokens,
        savings_rate=savings_rate,
        debug=debug or {},
    )


def resolve_inject_budget(
    config: dict[str, Any],
    inject_path: InjectPath,
    *,
    total_request_tokens: int,
    savings_tokens: int = 0,
    savings_rate: float | None = None,
    budget_state: SkillsGlobalState | None = None,
) -> SkillsInjectBudget:
    """Full budget pipeline: per-request math then optional DB global cap."""
    if not skills_budget_precheck(config):
        return SkillsInjectBudget(
            inject_path=inject_path,
            total_request_tokens=total_request_tokens,
            per_request_budget=0,
            effective_max=0,
            limit_global_remaining=0,
            bootstrap=True,
            savings_tokens=savings_tokens,
            savings_rate=savings_rate or 0.0,
        )

    per_request, debug = compute_per_request_budget(
        config,
        inject_path,
        total_request_tokens=total_request_tokens,
        savings_tokens=savings_tokens,
        savings_rate=savings_rate,
    )
    if per_request <= 0:
        return SkillsInjectBudget(
            inject_path=inject_path,
            total_request_tokens=total_request_tokens,
            per_request_budget=0,
            effective_max=0,
            limit_global_remaining=0,
            bootstrap=True,
            savings_tokens=savings_tokens,
            savings_rate=savings_rate or 0.0,
            debug=debug,
        )

    rate = savings_rate
    if rate is None and total_request_tokens > 0:
        rate = savings_tokens / total_request_tokens

    return apply_global_budget_cap(
        config,
        inject_path,
        per_request,
        budget_state=budget_state,
        total_request_tokens=total_request_tokens,
        savings_tokens=savings_tokens,
        savings_rate=rate or 0.0,
        debug=debug,
    )


def proxy_pre_pruner_budget_allows(
    config: dict[str, Any],
    body: dict[str, Any],
    *,
    kind: str,
) -> bool:
    """Gate before rerank/LLM skills pruner (savings unknown — use request fraction only)."""
    if not skills_inject_allowed(config, "proxy"):
        return False
    if not skills_budget_precheck(config):
        return False
    total = count_upstream_body_tokens(body, kind=kind)
    budget = resolve_inject_budget(
        config,
        "proxy",
        total_request_tokens=total,
        savings_tokens=0,
        savings_rate=0.0,
    )
    return budget.effective_max > 0


def format_skills_budget_report(
    config: dict[str, Any],
    *,
    budget_state: SkillsGlobalState | None = None,
    example_request_tokens: int = 100_000,
    example_savings_tokens: int = 10_000,
    example_savings_rate: float | None = None,
) -> str:
    from cyt.proxy.stats import StatsDB

    state = budget_state
    if state is None:
        db = StatsDB.open(stats_db_path(config))
        try:
            state = db.query_skills_budget_state(config, skills_inject_via(config))
        finally:
            db.close()

    active = skills_inject_via(config)
    enabled = skills_enabled(config)
    max_tokens = skills_max_tokens_per_request(config)
    lines = [
        "skills inject budget",
        "",
        "settings:",
        f"  skills.enabled: {enabled}",
        f"  skills.inject_via: {active}  (active path)",
        f"  max_tokens_per_request: {max_tokens}",
        f"  bm25_node_fallback_threshold: {skills_bm25_node_fallback_threshold(config)}",
        "",
        "  hook.request_budget_fraction:",
        f"    {skills_hook_request_budget_fraction(config)}",
        "  hook.inject_cap_multiplier_of_request_tokens:",
        f"    {skills_hook_inject_cap_multiplier(config)}",
        "",
        "  proxy.request_budget_fraction:",
        f"    {skills_proxy_request_budget_fraction(config)}",
        "  proxy.inject_cap_fraction_of_savings:",
        f"    {skills_proxy_inject_cap_fraction(config)}",
        "  proxy.savings_budget_fraction:",
        f"    {skills_proxy_savings_budget_fraction(config)}",
        "  proxy.savings_rate_threshold:",
        f"    {skills_proxy_savings_rate_threshold(config)}",
        "",
        "stats:",
        f"  skills_injected_total: {state.skills_injected_total:,}",
        f"  cumulative_request_tokens: {state.cumulative_request_tokens:,}",
        f"  prior_net_savings_tokens: {state.prior_net_savings_tokens:,}",
        "",
        "global lifetime caps:",
    ]

    hook_allowed = int(
        state.cumulative_request_tokens * skills_hook_inject_cap_multiplier(config),
    )
    proxy_allowed = int(
        state.prior_net_savings_tokens * skills_proxy_inject_cap_fraction(config),
    )
    hook_remaining = max(0, hook_allowed - state.skills_injected_total)
    proxy_remaining = max(0, proxy_allowed - state.skills_injected_total)
    lines.extend(
        [
            f"  hook:  allowed {hook_allowed:,}  spent {state.skills_injected_total:,}  "
            f"remaining {hook_remaining:,}" + ("  (bootstrap)" if state.bootstrap else ""),
            f"  proxy: allowed {proxy_allowed:,}  spent {state.skills_injected_total:,}  "
            f"remaining {proxy_remaining:,}" + ("  (bootstrap)" if state.bootstrap else ""),
            "",
        ],
    )

    blockers: list[str] = []
    if not enabled:
        blockers.append("skills.enabled is false — injection disabled in config")
    if max_tokens <= 0:
        blockers.append("max_tokens_per_request <= 0 — hard ceiling disabled")
    if not state.bootstrap and active == "hook" and hook_remaining <= 0:
        blockers.append("hook global cap exhausted")
    if not state.bootstrap and active == "proxy" and proxy_remaining <= 0:
        blockers.append("proxy global cap exhausted")

    will_inject = not blockers and enabled and max_tokens > 0
    lines.append(f"active path ({active}):")
    lines.append(f"  injection will work: {'yes' if will_inject else 'no'}")
    for reason in blockers:
        lines.append(f"  blocker: {reason}")
    lines.append("")

    hook_example_budget = resolve_inject_budget(
        config,
        "hook",
        total_request_tokens=example_request_tokens,
        budget_state=state,
    )
    rate = example_savings_rate
    if rate is None and example_request_tokens > 0:
        rate = example_savings_tokens / example_request_tokens
    rate = rate or 0.0
    proxy_example_budget = resolve_inject_budget(
        config,
        "proxy",
        total_request_tokens=example_request_tokens,
        savings_tokens=example_savings_tokens,
        savings_rate=rate,
        budget_state=state,
    )

    hook_label = "" if active == "hook" else " (inactive — inject_via: proxy)"
    lines.extend(
        [
            f"hook example{hook_label}\n"
            f"User's prompt + Agent's last message request_tokens = {example_request_tokens:,}:",
            f"  per_request = floor({example_request_tokens:,} x "
            f"{skills_hook_request_budget_fraction(config)}) = {hook_example_budget.debug.get('limit_request', 0):,}",
            f"  effective_max = {hook_example_budget.effective_max:,} tokens",
            "",
        ],
    )

    proxy_label = "" if active == "proxy" else " (inactive — inject_via: hook)"
    lines.extend(
        [
            f"proxy example{proxy_label}\n"
            f"Full HTTP body request={example_request_tokens:,}, savings={example_savings_tokens:,}, "
            f"rate={rate:.0%}:",
            f"  limit_savings  = {proxy_example_budget.debug.get('limit_savings', 0):,}",
            f"  limit_request  = {proxy_example_budget.debug.get('limit_request', 0):,}",
            f"  limit_marginal = {proxy_example_budget.debug.get('limit_marginal', 0):,}",
            f"  effective_max  = {proxy_example_budget.effective_max:,} tokens",
        ],
    )
    return "\n".join(lines)


def skills_budget_report_json(
    config: dict[str, Any],
    *,
    budget_state: SkillsGlobalState | None = None,
    example_request_tokens: int = 100_000,
    example_savings_tokens: int = 10_000,
    example_savings_rate: float | None = None,
) -> dict[str, Any]:
    from cyt.proxy.stats import StatsDB

    state = budget_state
    if state is None:
        db = StatsDB.open(stats_db_path(config))
        try:
            state = db.query_skills_budget_state(config, skills_inject_via(config))
        finally:
            db.close()

    active = skills_inject_via(config)
    rate = example_savings_rate
    if rate is None and example_request_tokens > 0:
        rate = example_savings_tokens / example_request_tokens

    hook_budget = resolve_inject_budget(
        config,
        "hook",
        total_request_tokens=example_request_tokens,
        budget_state=state,
    )
    proxy_budget = resolve_inject_budget(
        config,
        "proxy",
        total_request_tokens=example_request_tokens,
        savings_tokens=example_savings_tokens,
        savings_rate=rate or 0.0,
        budget_state=state,
    )
    hook_allowed = int(
        state.cumulative_request_tokens * skills_hook_inject_cap_multiplier(config),
    )
    proxy_allowed = int(
        state.prior_net_savings_tokens * skills_proxy_inject_cap_fraction(config),
    )
    blockers: list[str] = []
    if not skills_enabled(config):
        blockers.append("skills.enabled is false")
    if skills_max_tokens_per_request(config) <= 0:
        blockers.append("max_tokens_per_request <= 0")

    return {
        "inject_via": active,
        "skills_enabled": skills_enabled(config),
        "global": {
            "hook": {
                "allowed": hook_allowed,
                "spent": state.skills_injected_total,
                "remaining": max(0, hook_allowed - state.skills_injected_total),
                "cap_reached": not state.bootstrap
                and max(0, hook_allowed - state.skills_injected_total) <= 0,
            },
            "proxy": {
                "allowed": proxy_allowed,
                "spent": state.skills_injected_total,
                "remaining": max(0, proxy_allowed - state.skills_injected_total),
                "cap_reached": not state.bootstrap
                and max(0, proxy_allowed - state.skills_injected_total) <= 0,
            },
        },
        "active_path": {
            "path": active,
            "will_inject": not blockers,
            "block_reasons": blockers,
        },
        "examples": {
            "hook": {
                "request_tokens": example_request_tokens,
                "per_request": hook_budget.per_request_budget,
                "effective_max": hook_budget.effective_max,
            },
            "proxy": {
                "request_tokens": example_request_tokens,
                "savings_tokens": example_savings_tokens,
                "savings_rate": rate or 0.0,
                "limits": proxy_budget.debug,
                "per_request": proxy_budget.per_request_budget,
                "effective_max": proxy_budget.effective_max,
            },
        },
    }

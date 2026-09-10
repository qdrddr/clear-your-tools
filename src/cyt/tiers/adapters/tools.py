"""PolicyContext integration for tool tiers."""

from __future__ import annotations

import copy
from typing import Any

from cyt.config.policy_catalog import ToolPolicyRef
from cyt.indexer.policies import PolicyContext
from cyt.tiers.models import Tier, ToolsTierApplyResult


def resolve_tool_catalog_source(tool: dict[str, Any]) -> str:
    """Infer catalog source when ``cyt_catalog_source`` was not stamped on the tool dict."""
    explicit = str(tool.get("cyt_catalog_source") or "").strip()
    if explicit:
        return explicit
    name = str(tool.get("name") or "").strip()
    if tool.get("mcpc_session") or (name.startswith("@") and "/" in name):
        return "mcpc"
    if name.startswith("mcp__"):
        return "cyt_mcp"
    return "unknown"


def stamp_tool_catalog_source(tool: dict[str, Any]) -> dict[str, Any]:
    stamped = copy.deepcopy(tool)
    if not str(stamped.get("cyt_catalog_source") or "").strip():
        stamped["cyt_catalog_source"] = resolve_tool_catalog_source(stamped)
    return stamped


def tool_entity_id(tool: dict[str, Any]) -> str:
    name = str(tool.get("name") or "").strip()
    source = resolve_tool_catalog_source(tool)
    return f"{source}:{name}" if name else ""


def canonical_tool_entity_id(entity_id: str) -> str:
    """Map legacy ``unknown:<name>`` rows onto inferred ``<source>:<name>`` ids."""
    text = (entity_id or "").strip()
    if not text.startswith("unknown:"):
        return text
    name = text.split(":", 1)[1]
    resolved = tool_entity_id({"name": name})
    return resolved if resolved and not resolved.startswith("unknown:") else text


def _merge_tool_stats(target: Any, source: Any) -> None:
    target.candidates += source.candidates
    target.injected += source.injected
    target.used += source.used
    target.used_without_injection += source.used_without_injection
    target.optional_used += source.optional_used
    target.shadow_hits += source.shadow_hits
    target.shadow_evaluations += source.shadow_evaluations
    target.last_seen_ms = max(target.last_seen_ms, source.last_seen_ms)


def normalize_tool_entity_states(
    states: dict[tuple[str, str], Any],
) -> tuple[list[str], list[Any]]:
    """Merge legacy ``unknown:*`` tool rows onto canonical catalog-scoped entity ids."""
    from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState

    removed: list[str] = []
    updated: list[EntityTierState] = []
    touched: set[str] = set()

    for key, state in list(states.items()):
        if key[0] != EntityKind.TOOL or not isinstance(state, EntityTierState):
            continue
        canonical = canonical_tool_entity_id(state.entity_id)
        if canonical == state.entity_id:
            continue
        canonical_key = (EntityKind.TOOL, canonical)
        target = states.get(canonical_key)
        if target is None:
            target = EntityTierState(
                entity_id=canonical,
                kind=EntityKind.TOOL,
                stable_tier=state.stable_tier,
                effective_tier=state.effective_tier,
                overlap_tier=state.overlap_tier,
                tier_since_epoch=state.tier_since_epoch,
                temp_promotion_until_ms=state.temp_promotion_until_ms,
                wake_lease_until_session=state.wake_lease_until_session,
                sleep_cooldown_until_session=state.sleep_cooldown_until_session,
                pipeline=state.pipeline,
                stats=EffectiveStats(
                    candidates=state.stats.candidates,
                    injected=state.stats.injected,
                    used=state.stats.used,
                    used_without_injection=state.stats.used_without_injection,
                    optional_used=state.stats.optional_used,
                    shadow_hits=state.stats.shadow_hits,
                    shadow_evaluations=state.stats.shadow_evaluations,
                    last_seen_ms=state.stats.last_seen_ms,
                    requests_since_decay=state.stats.requests_since_decay,
                ),
            )
            states[canonical_key] = target
        else:
            _merge_tool_stats(target.stats, state.stats)
        touched.add(canonical)
        removed.append(state.entity_id)
        del states[key]

    for key, state in states.items():
        if key[0] != EntityKind.TOOL or not isinstance(state, EntityTierState):
            continue
        if state.entity_id in touched:
            updated.append(state)
    deduped: list[EntityTierState] = []
    seen: set[str] = set()
    for state in updated:
        if state.entity_id in seen:
            continue
        seen.add(state.entity_id)
        deduped.append(state)
    return removed, deduped


def apply_tool_tiers(
    tools: list[dict[str, Any]],
    *,
    tier_for_tool: dict[str, Tier],
    apply: bool,
) -> ToolsTierApplyResult:
    eligible: list[dict[str, Any]] = []
    t4_direct: list[dict[str, Any]] = []
    excluded_t0: list[str] = []
    policy_overrides: dict[str, ToolPolicyRef] = {}
    tier_by_tool: dict[str, Tier] = {}

    for tool in tools:
        entity_id = tool_entity_id(tool)
        tool_name = str(tool.get("name") or "")
        if not entity_id:
            eligible.append(tool)
            continue
        tier = tier_for_tool.get(entity_id, Tier.ACTIVE)
        tier_by_tool[entity_id] = tier
        if not apply:
            eligible.append(tool)
            continue
        if tier == Tier.DORMANT:
            excluded_t0.append(entity_id)
            continue
        if tier == Tier.EXTRA_HOT:
            t4_direct.append(copy.deepcopy(tool))
            policy_overrides[tool_name] = "always_include"
            continue
        eligible.append(tool)
        policy_name = {
            Tier.COLD: "tier_cold",
            Tier.ACTIVE: "tier_active",
            Tier.HOT: "tier_hot",
        }.get(tier)
        if policy_name:
            policy_overrides[tool_name] = policy_name

    return ToolsTierApplyResult(
        eligible_tools=eligible,
        t4_direct=t4_direct,
        excluded_t0=excluded_t0,
        policy_overrides=policy_overrides,
        tier_by_tool=tier_by_tool,
    )


def merge_tool_policies(
    output_ctx: PolicyContext,
    policy_overrides: dict[str, ToolPolicyRef],
) -> PolicyContext:
    if not policy_overrides:
        return output_ctx
    merged_per_tool = dict(output_ctx.per_tool or {})
    merged_per_tool.update(policy_overrides)
    return PolicyContext(
        system_tool=output_ctx.system_tool,
        mcp_tool=output_ctx.mcp_tool,
        per_tool=merged_per_tool,
        tool_kind=output_ctx.tool_kind,
    )


def merge_t4_tools(
    pruned: list[dict[str, Any]],
    t4_direct: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not t4_direct:
        return pruned
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for tool in pruned:
        name = str(tool.get("name") or "")
        if name and name not in by_name:
            order.append(name)
        if name:
            by_name[name] = tool
    for tool in t4_direct:
        name = str(tool.get("name") or "")
        if name and name not in order:
            order.append(name)
        if name:
            by_name[name] = tool
    return [by_name[name] for name in order if name in by_name]

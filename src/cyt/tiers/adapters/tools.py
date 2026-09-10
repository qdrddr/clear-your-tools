"""PolicyContext integration for tool tiers."""

from __future__ import annotations

import copy
from typing import Any

from cyt.config.policy_catalog import ToolPolicyRef
from cyt.indexer.policies import PolicyContext
from cyt.tiers.models import Tier, ToolsTierApplyResult


def tool_entity_id(tool: dict[str, Any]) -> str:
    name = str(tool.get("name") or "").strip()
    source = str(tool.get("cyt_catalog_source") or "unknown").strip()
    return f"{source}:{name}" if name else ""


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

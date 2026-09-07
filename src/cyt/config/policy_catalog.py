"""Declarative tool policy catalog — merge, resolve, enum compatibility."""

from __future__ import annotations

import copy
from typing import Any, Literal, TypedDict, cast

ToolPolicy = Literal[
    "always_include",
    "prune_optional",
    "prune_all",
    "prune_optional_descriptions",
    "prune_all_descriptions",
]

POLICY_CHOICES: tuple[ToolPolicy, ...] = (
    "always_include",
    "prune_optional",
    "prune_all",
    "prune_optional_descriptions",
    "prune_all_descriptions",
)
VALID_TOOL_POLICIES: frozenset[str] = frozenset(POLICY_CHOICES)


class RetainSpec(TypedDict, total=False):
    tool: list[str]
    required_properties: list[str]
    optional_properties: list[str]


class PolicyScoring(TypedDict, total=False):
    tool_root: Literal["pin", "when_relevant"]
    optional_properties: Literal["when_relevant"]


class PolicyConditional(TypedDict, total=False):
    triggers: list[str]
    retain: RetainSpec


class PolicyDef(TypedDict, total=False):
    name: str
    description: str
    mode: Literal["always_include", "prune"]
    scoring: PolicyScoring
    reinstate_scored_away_optionals: bool
    always: RetainSpec
    conditional: list[PolicyConditional]
    when_tool_pruned: RetainSpec


def _policy_entry_name(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def merge_policies_by_name(base: list[Any], overlay: list[Any]) -> list[dict[str, Any]]:
    """Merge policy lists by ``name`` — overlay entries replace same-named base entries."""
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def ingest(items: list[Any]) -> None:
        for item in items:
            name = _policy_entry_name(item)
            if name is None:
                continue
            if name not in by_name:
                order.append(name)
                by_name[name] = copy.deepcopy(cast(dict[str, Any], item))
            else:
                merged_entry = copy.deepcopy(by_name[name])
                overlay_entry = copy.deepcopy(cast(dict[str, Any], item))
                for key, value in overlay_entry.items():
                    merged_entry[key] = value
                by_name[name] = merged_entry

    ingest(base)
    ingest(overlay)
    return [by_name[name] for name in order]


def _policies_list(cfg: dict[str, Any]) -> list[Any]:
    raw = cfg.get("policies")
    return list(raw) if isinstance(raw, list) else []


def apply_policy_catalog_merge(
    merged: dict[str, Any],
    *,
    bundled: dict[str, Any],
    overlay: dict[str, Any],
) -> None:
    """Replace ``merged['policies']`` with name-merged catalog from bundled + overlay."""
    base = _policies_list(bundled)
    over = _policies_list(overlay)
    if not base and not over:
        merged.pop("policies", None)
        return
    merged["policies"] = merge_policies_by_name(base, over)


def resolved_policies(config: dict[str, Any]) -> dict[str, PolicyDef]:
    """Return policy definitions keyed by ``name`` from merged config."""
    raw = config.get("policies")
    if not isinstance(raw, list):
        return {}
    out: dict[str, PolicyDef] = {}
    for item in raw:
        name = _policy_entry_name(item)
        if name is None:
            continue
        out[name] = cast(PolicyDef, item)
    return out


def resolve_policy(name: str, config: dict[str, Any]) -> PolicyDef | None:
    """Look up a policy definition by name."""
    return resolved_policies(config).get(name.strip())


def validate_policy_reference(name: str, config: dict[str, Any]) -> bool:
    """Return True when *name* exists in the resolved policy catalog."""
    text = name.strip()
    if text in VALID_TOOL_POLICIES:
        catalog = resolved_policies(config)
        if catalog and text not in catalog:
            return False
        return True
    return text in resolved_policies(config)


def policy_def_to_enum(policy: PolicyDef | None, *, fallback_name: str) -> ToolPolicy:
    """Map a declarative policy definition to legacy enum semantics (Phase A shim)."""
    if policy is None:
        if fallback_name in VALID_TOOL_POLICIES:
            return cast(ToolPolicy, fallback_name)
        return "prune_optional"

    name = str(policy.get("name") or fallback_name).strip()
    if policy.get("mode") == "always_include" or name == "always_include":
        return "always_include"

    if name in VALID_TOOL_POLICIES:
        return cast(ToolPolicy, name)

    scoring = policy.get("scoring")
    if not isinstance(scoring, dict):
        return "prune_optional"

    tool_root = scoring.get("tool_root")
    reinstate = bool(policy.get("reinstate_scored_away_optionals"))

    if tool_root == "pin":
        return "prune_optional_descriptions" if reinstate else "prune_optional"
    if tool_root == "when_relevant":
        return "prune_all_descriptions" if reinstate else "prune_all"
    return "prune_optional"


def resolve_policy_as_enum(name: str, config: dict[str, Any]) -> ToolPolicy:
    """Resolve policy name → definition → legacy enum for Rust-backed pruning."""
    text = name.strip()
    policy = resolve_policy(text, config)
    return policy_def_to_enum(policy, fallback_name=text)

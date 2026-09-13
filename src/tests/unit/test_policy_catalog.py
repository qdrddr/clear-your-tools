#!/usr/bin/env python3
"""Tests for declarative policy catalog merge and resolution."""

from __future__ import annotations

from cyt.config import _config_with_bundled_defaults, load_config
from cyt.config.policy_catalog import (
    merge_policies_by_name,
    policy_def_to_enum,
    resolve_policy,
    resolved_policies,
)


def test_merge_policies_by_name_overlays_by_name() -> None:
    base = [{"name": "prune_optional", "mode": "prune", "scoring": {"tool_root": "pin"}}]
    overlay = [{"name": "prune_optional", "description": "custom"}]
    merged = merge_policies_by_name(base, overlay)
    assert len(merged) == 1
    assert merged[0]["name"] == "prune_optional"
    assert merged[0]["description"] == "custom"
    assert merged[0]["mode"] == "prune"


def test_bundled_defaults_include_policy_catalog() -> None:
    cfg = _config_with_bundled_defaults({})
    names = {item["name"] for item in cfg.get("policies", []) if isinstance(item, dict)}
    assert names >= {
        "always_include",
        "prune_optional",
        "prune_all",
        "prune_optional_descriptions",
        "prune_all_descriptions",
    }


def test_resolve_policy_as_enum_from_definition() -> None:
    cfg = load_config()
    policy = resolve_policy("prune_all_descriptions", cfg)
    assert policy is not None
    assert (
        policy_def_to_enum(policy, fallback_name="prune_all_descriptions")
        == "prune_all_descriptions"
    )


def test_tier_cold_allows_bm25_to_drop_tool_roots() -> None:
    cfg = load_config()
    policy = resolve_policy("tier_cold", cfg)
    assert policy is not None
    scoring = policy.get("scoring")
    assert isinstance(scoring, dict)
    assert scoring.get("tool_root") == "when_relevant"
    assert policy_def_to_enum(policy, fallback_name="tier_cold") == "prune_all"


def test_tier_hot_reinstates_scored_away_optionals() -> None:
    cfg = load_config()
    policy = resolve_policy("tier_hot", cfg)
    assert policy is not None
    assert policy.get("reinstate_scored_away_optionals") is True
    assert policy_def_to_enum(policy, fallback_name="tier_hot") == "prune_all_descriptions"


def test_tier_cold_and_active_map_to_prune_all_not_pin() -> None:
    cfg = load_config()
    for name in ("tier_cold", "tier_active"):
        policy = resolve_policy(name, cfg)
        assert policy is not None
        scoring = policy.get("scoring")
        assert isinstance(scoring, dict)
        assert scoring.get("tool_root") == "when_relevant"
        assert policy_def_to_enum(policy, fallback_name=name) == "prune_all"


def test_user_policy_overlay_merges_not_replaces() -> None:
    cfg = _config_with_bundled_defaults(
        {
            "policies": [
                {
                    "name": "prune_optional",
                    "description": "user override",
                    "mode": "prune",
                    "scoring": {"tool_root": "pin", "optional_properties": "when_relevant"},
                },
            ],
        },
    )
    catalog = resolved_policies(cfg)
    assert catalog["prune_optional"].get("description") == "user override"
    assert "prune_all" in catalog

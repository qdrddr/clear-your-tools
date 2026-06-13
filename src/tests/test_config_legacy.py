#!/usr/bin/env python3
"""Legacy config path resolution tests."""

from __future__ import annotations

from typing import Any

import pytest

import cyt.config as configs
from cyt.config import legacy


@pytest.mark.parametrize(
    ("legacy_name", "user_config", "expected"),
    [
        (
            "tools.sequence",
            {"pruning": {"pipeline": ["rerank", "llm"]}},
            ["rerank", "llm"],
        ),
        (
            "tools.policy.system_tool",
            {"defaults": {"system_tool_policy": "always_include"}},
            "always_include",
        ),
        (
            "tools.policy.mcp_tool",
            {"pruning": {"policy": {"mcp_tool": "prune_optional"}}},
            "prune_optional",
        ),
        (
            "tools.policy.minimum_tools",
            {"models": {"llm": {"minimum_tools": 40}}},
            40,
        ),
        (
            "pipelines.rerank.model_nick",
            {
                "pruning": {
                    "rerank": {"model": {"remote": {"model_nick": "legacy-rerank"}}},
                },
            },
            "legacy-rerank",
        ),
        (
            "pipelines.llm.model_nick",
            {"defaults": {"remote": {"llm_model_nick": "legacy-llm"}}},
            "legacy-llm",
        ),
        (
            "pipelines.bm25.index_dir",
            {"models": {"bm25": {"index_dir": "/tmp/models-bm25"}}},
            "/tmp/models-bm25",
        ),
    ],
)
def test_legacy_resolve_user_overlay(
    legacy_name: str,
    user_config: dict,
    expected: object,
) -> None:
    value = legacy.resolve_legacy({}, user_config, legacy_name)
    assert value == expected


def test_pruning_pipeline_reads_tools_sequence() -> None:
    config = {"pruning": {"tools": {"sequence": ["bm25", "rerank"]}}}
    assert configs.pruning_pipeline_from_config(config) == ["bm25", "rerank"]


def test_pruning_pipeline_legacy_pipeline_fallback() -> None:
    config = {"pruning": {"pipeline": ["llm"]}}
    assert configs.pruning_pipeline_from_config(config) == ["llm"]


def test_merge_model_entry_inherits_provider_fields() -> None:
    config = {
        "models": {
            "providers": [
                {
                    "provider_nick": "anthropic",
                    "provider": "anthropic",
                    "key_var_name": "ANTHROPIC_API_KEY",
                    "domain_match": ["api.anthropic.com"],
                },
            ],
            "llm": {
                "remote": [
                    {
                        "nick": "haiku45",
                        "name": "claude-haiku-4-5",
                        "provider_nick": "anthropic",
                    },
                ],
            },
        },
    }
    entry = configs.remote_model_entry(config, "llm", "haiku45")
    assert entry["provider"] == "anthropic"
    assert entry["key_var_name"] == "ANTHROPIC_API_KEY"
    assert configs.litellm_model_name(entry) == "anthropic/claude-haiku-4-5"


def test_merge_model_entry_resolves_bundled_provider_registry() -> None:
    config = {
        "models": {
            "llm": {
                "remote": [
                    {
                        "nick": "sonnet",
                        "name": "claude-sonnet-4-6",
                        "provider_nick": "anthropic",
                    },
                ],
            },
        },
    }
    entry = configs.merge_model_entry(config, config["models"]["llm"]["remote"][0])
    assert entry["domain_match"] == ["api.anthropic.com"]
    assert entry["key_var_name"] == "ANTHROPIC_API_KEY"


def test_canonical_tools_policy_when_legacy_absent() -> None:
    config = {
        "pruning": {
            "tools": {"policy": {"system_tool": "always_include"}},
        },
    }
    assert configs.pruning_system_tool_policy(config) == "always_include"


def test_legacy_tools_policy_wins_when_both_present() -> None:
    config = {
        "pruning": {
            "tools": {"policy": {"system_tool": "always_include"}},
            "policy": {"system_tool": "prune_all"},
        },
    }
    assert configs.pruning_system_tool_policy(config) == "prune_all"


def test_stats_provider_for_entry_uses_registry_provider() -> None:
    config: dict[str, Any] = {
        "models": {
            "providers": [
                {
                    "provider_nick": "my-router",
                    "provider": "openrouter",
                    "domain_match": ["openrouter.ai"],
                },
            ],
            "llm": {
                "remote": [
                    {
                        "name": "google/gemini-3-flash-preview",
                        "provider_nick": "my-router",
                    },
                ],
            },
        },
    }
    entry = config["models"]["llm"]["remote"][0]
    assert configs.stats_provider_for_entry(config, entry) == "openrouter"


def test_stats_provider_for_entry_ignores_inline_model_provider() -> None:
    config: dict[str, Any] = {
        "models": {
            "providers": [
                {
                    "provider_nick": "my-router",
                    "provider": "openrouter",
                    "domain_match": ["openrouter.ai"],
                },
            ],
            "llm": {
                "remote": [
                    {
                        "name": "google/gemini-3-flash-preview",
                        "provider_nick": "my-router",
                        "provider": "wrong-inline-provider",
                    },
                ],
            },
        },
    }
    entry = configs.merge_model_entry(config, config["models"]["llm"]["remote"][0])
    assert entry["provider"] == "wrong-inline-provider"
    assert configs.stats_provider_for_entry(config, entry) == "openrouter"


def test_lookup_model_provider_resolves_from_provider_nick() -> None:
    from cyt.proxy.stats import lookup_model_provider, lookup_provider_from_dns

    config = {
        "models": {
            "providers": [
                {
                    "provider_nick": "openrouter",
                    "provider": "openrouter",
                    "domain_match": ["openrouter.ai"],
                },
            ],
            "llm": {
                "remote": [
                    {
                        "name": "google/gemini-3-flash-preview",
                        "provider_nick": "openrouter",
                    },
                ],
            },
        },
    }
    provider, dns = lookup_model_provider("google/gemini-3-flash-preview", config)
    assert provider == "openrouter"
    assert dns == "openrouter.ai"
    assert lookup_provider_from_dns("api.openrouter.ai", config) == "openrouter"


def test_stats_provider_for_entry_returns_none_when_registry_missing() -> None:
    config: dict[str, Any] = {
        "models": {
            "llm": {
                "remote": [
                    {
                        "name": "unknown-model",
                        "provider_nick": "missing-provider",
                    },
                ],
            },
        },
    }
    entry = config["models"]["llm"]["remote"][0]
    assert configs.stats_provider_for_entry(config, entry) is None


def test_lookup_model_provider_returns_none_provider_when_registry_missing() -> None:
    from cyt.proxy.stats import lookup_model_provider, lookup_provider_from_dns

    config: dict[str, Any] = {
        "models": {
            "llm": {
                "remote": [
                    {
                        "name": "unknown-model",
                        "provider_nick": "missing-provider",
                    },
                ],
            },
        },
    }
    provider, dns = lookup_model_provider("unknown-model", config)
    assert provider is None
    assert dns is None
    assert lookup_provider_from_dns("api.unknown.example", config) is None

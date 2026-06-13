#!/usr/bin/env python3
"""Config resolution and provider registry tests."""

from __future__ import annotations

from typing import Any

import cyt.config as configs


def test_pruning_pipeline_reads_tools_sequence() -> None:
    config = {"pruning": {"tools": {"sequence": ["bm25", "rerank"]}}}
    assert configs.pruning_pipeline_from_config(config) == ["bm25", "rerank"]


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


def test_pruning_system_tool_policy_from_canonical_path() -> None:
    config = {
        "pruning": {
            "tools": {"policy": {"system_tool": "always_include"}},
        },
    }
    assert configs.pruning_system_tool_policy(config) == "always_include"


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

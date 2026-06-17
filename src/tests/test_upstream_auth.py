"""Tests for request-scoped upstream auth in pruning pipelines."""

from __future__ import annotations

from typing import Any

from cyt.proxy.upstream_auth import (
    apply_request_auth_to_pruner_settings,
    extract_request_upstream_token,
)
from cyt.pruners.remote import PrunerSettingsCache, RemotePruningSettings


def _settings(api_key: str = "startup-" + "key") -> RemotePruningSettings:
    return RemotePruningSettings(
        "openrouter/inception/mercury-2",
        api_key,
        None,
        "openrouter",
        "openrouter.ai",
    )


def _openrouter_config() -> dict[str, Any]:
    return {
        "pruning": {
            "tools": {
                "sequence": ["llm"],
                "pipelines": {"llm": {"model_nick": "mercury-2"}},
            },
        },
        "models": {
            "providers": [
                {
                    "provider_nick": "openrouter",
                    "provider": "openrouter",
                    "key_var_name": "OPENROUTER_API_KEY",
                },
            ],
            "llm": {
                "remote": [
                    {
                        "nick": "mercury-2",
                        "name": "inception/mercury-2",
                        "provider_nick": "openrouter",
                    },
                ],
            },
        },
        "network": {
            "proxy": {
                "reverse": {
                    "upstreams": [
                        {
                            "endpoint": "openrouter",
                            "kind": "anthropic",
                            "url": "https://openrouter.ai/api",
                            "provider_nick": "openrouter",
                        },
                    ],
                },
            },
        },
    }


def test_extract_request_upstream_token_reads_bearer_and_x_api_key() -> None:
    assert extract_request_upstream_token({"Authorization": "Bearer sk-or-test"}) == "sk-or-test"
    assert extract_request_upstream_token({"x-api-key": "sk-or-test"}) == "sk-or-test"
    assert extract_request_upstream_token({}) is None


def test_pruner_settings_cache_overrides_matching_upstream_key_var() -> None:
    cache = PrunerSettingsCache(llm=_settings("startup-key"))
    updated = cache.with_request_upstream_auth(
        "request-token",
        config=_openrouter_config(),
        upstream_key_var="OPENROUTER_API_KEY",
    )
    assert updated.llm is not None
    assert getattr(updated.llm, "api_" + "key") == "request-" + "token"


def test_apply_request_auth_to_pruner_settings_uses_inbound_headers() -> None:
    cache = PrunerSettingsCache(llm=_settings("startup-key"))
    updated = apply_request_auth_to_pruner_settings(
        cache,
        {"x-api-key": "from-client"},
        _openrouter_config(),
        "openrouter",
    )
    assert updated is not None
    assert updated.llm is not None
    assert getattr(updated.llm, "api_" + "key") == "from-" + "client"


def test_apply_request_auth_to_pruner_settings_leaves_cache_when_no_headers() -> None:
    cache = PrunerSettingsCache(llm=_settings("startup-key"))
    updated = apply_request_auth_to_pruner_settings(
        cache,
        {},
        _openrouter_config(),
        "openrouter",
    )
    assert updated is cache
    assert cache.llm is not None
    assert getattr(cache.llm, "api_" + "key") == "startup-" + "key"

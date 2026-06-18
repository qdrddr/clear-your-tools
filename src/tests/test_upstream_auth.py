"""Tests for request-scoped upstream auth in pruning pipelines."""

from __future__ import annotations

from typing import Any

import pytest

from cyt.proxy.upstream_auth import (
    apply_request_auth_to_pruner_settings,
    extract_request_upstream_token,
    prepare_forward_headers,
    resolve_upstream_auth_token,
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


def test_apply_request_auth_to_pruner_settings_leaves_cache_when_no_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_" + "API_KEY", raising=False)
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


def test_apply_request_auth_does_not_apply_client_token_across_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_" + "API_KEY", raising=False)
    config = _openrouter_config()
    config["network"]["proxy"]["reverse"]["upstreams"].insert(
        0,
        {
            "endpoint": "anthropic",
            "kind": "anthropic",
            "url": "https://api.anthropic.com",
            "provider_nick": "anthropic",
        },
    )
    config["models"]["providers"].append(
        {
            "provider_nick": "anthropic",
            "provider": "anthropic",
            "key_var_name": "ANTHROPIC_API_KEY",
        },
    )

    cache = PrunerSettingsCache(llm=_settings("openrouter-startup-key"))
    updated = apply_request_auth_to_pruner_settings(
        cache,
        {"x-api-key": "sk-ant-client-key"},
        config,
        "anthropic",
    )
    assert updated is not None
    assert updated.llm is not None
    assert getattr(updated.llm, "api_" + "key") == "openrouter-startup-key"


def test_apply_request_auth_applies_client_token_to_same_provider_pipeline_key_vars() -> None:
    custom_key_var = "MY_LLM_GATEWAY_KEY"
    config: dict[str, Any] = {
        "pruning": {
            "tools": {
                "sequence": ["llm"],
                "pipelines": {"llm": {"model_nick": "gateway-model"}},
            },
        },
        "models": {
            "providers": [
                {
                    "provider_nick": "gateway",
                    "provider": "openai",
                    "key_var_name": custom_key_var,
                },
            ],
            "llm": {
                "remote": [
                    {
                        "nick": "gateway-model",
                        "name": "gpt-4o-mini",
                        "provider_nick": "gateway",
                    },
                ],
            },
        },
        "network": {
            "proxy": {
                "reverse": {
                    "upstreams": [
                        {
                            "endpoint": "gateway",
                            "kind": "openai",
                            "url": "https://gateway.example.com",
                            "provider_nick": "gateway",
                        },
                    ],
                },
            },
        },
    }

    cache = PrunerSettingsCache(
        llm=RemotePruningSettings(
            "openai/gpt-4o-mini",
            "startup-key",
            None,
            "openai",
            "gateway.example.com",
        ),
    )
    client_token = "gateway-" + "client-token"
    updated = apply_request_auth_to_pruner_settings(
        cache,
        {"Authorization": f"Bearer {client_token}"},
        config,
        "gateway",
    )
    assert updated is not None
    assert updated.llm is not None
    assert getattr(updated.llm, "api_" + "key") == client_token


def test_apply_request_auth_to_pruner_settings_falls_back_to_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = PrunerSettingsCache(llm=_settings("startup-key"))
    monkeypatch.setenv("OPENROUTER_" + "API_KEY", "env-" + "token")
    updated = apply_request_auth_to_pruner_settings(
        cache,
        {},
        _openrouter_config(),
        "openrouter",
    )
    assert updated is not None
    assert updated.llm is not None
    assert getattr(updated.llm, "api_" + "key") == "env-" + "token"


def test_prepare_forward_headers_injects_env_auth_when_client_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_" + "API_KEY", "env-" + "token")
    headers = prepare_forward_headers(
        {"content-type": "application/json"},
        config=_openrouter_config(),
        endpoint_name="openrouter",
    )
    assert headers["Authorization"] == "Bearer env-token"
    assert headers["x-api-key"] == "env-token"


def test_prepare_forward_headers_replaces_empty_bearer_with_env_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_" + "API_KEY", "env-" + "token")
    headers = prepare_forward_headers(
        {"Authorization": "Bearer "},
        config=_openrouter_config(),
        endpoint_name="openrouter",
    )
    assert headers["Authorization"] == "Bearer env-token"
    assert headers["x-api-key"] == "env-token"


def test_prepare_forward_headers_prefers_client_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_" + "API_KEY", "env-" + "token")
    headers = prepare_forward_headers(
        {"x-api-key": "client-" + "token"},
        config=_openrouter_config(),
        endpoint_name="openrouter",
    )
    assert headers["Authorization"] == "Bearer client-token"
    assert headers["x-api-key"] == "client-token"


def test_resolve_upstream_auth_token_prefers_client_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_" + "API_KEY", "env-" + "token")
    token = resolve_upstream_auth_token(
        {"Authorization": "Bearer client-" + "token"},
        config=_openrouter_config(),
        endpoint_name="openrouter",
    )
    assert token == "client-" + "token"

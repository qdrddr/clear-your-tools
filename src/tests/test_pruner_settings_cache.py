"""Tests for startup pruner settings cache and config threading."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from cyt.common.token_usage import empty_usage
from cyt.pruners.remote import PrunerSettingsCache, RemotePruningSettings
from cyt.pruners.rerank import rerank_catalog_dict, rerank_pruning_settings


def _cached_rerank_settings() -> RemotePruningSettings:
    return RemotePruningSettings(
        "test-model",
        "cached-" + "key",
        "https://example.com",
        "test",
        "example.com",
    )


def test_rerank_pruning_settings_returns_cached_settings() -> None:
    cached = _cached_rerank_settings()
    assert rerank_pruning_settings(settings=cached) is cached


def test_rerank_catalog_dict_threads_config_and_cached_settings() -> None:
    config: dict[str, Any] = {
        "pruning": {
            "tools": {
                "sequence": ["rerank"],
                "policy": {"minimum_tools": 1},
            },
        },
    }
    cached = _cached_rerank_settings()
    data = {
        "json": [
            {"file_path": f"tool-{idx}.json", "description": f"tool {idx}"} for idx in range(5)
        ],
    }

    with patch("cyt.pruners.rerank.rerank_items", return_value=(data["json"], empty_usage())):
        with patch("cyt.pruners.rerank.rerank_pruning_settings") as settings_mock:
            settings_mock.return_value = cached
            rerank_catalog_dict(
                data,
                "query",
                config=config,
                settings=cached,
                prune=False,
            )
            settings_mock.assert_called_once_with(config, settings=cached)


def test_pruner_settings_cache_for_stage() -> None:
    cached = _cached_rerank_settings()
    cache = PrunerSettingsCache(rerank=cached)
    assert cache.for_stage("rerank") is cached
    assert cache.for_stage("llm") is None


def test_llm_pruning_settings_uses_request_scoped_cache() -> None:
    from cyt.pruners.llm import llm_pruning_settings
    from cyt.pruners.remote import push_request_pruner_settings, reset_request_pruner_settings

    cached = _cached_rerank_settings()
    cache = PrunerSettingsCache(llm=cached)
    token = push_request_pruner_settings(cache)
    try:
        assert llm_pruning_settings(settings=None) is cached
    finally:
        reset_request_pruner_settings(token)

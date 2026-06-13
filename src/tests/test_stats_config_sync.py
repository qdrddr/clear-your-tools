"""Tests for syncing stats DB model identities into user config."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from cyt.common.token_usage import StageTokenUsage
from cyt.proxy.stats import ProxyRequestRecord, StatsDB
from cyt.proxy.stats_config_sync import (
    ModelIdentity,
    _base_nick,
    build_model_entry,
    build_models_overlay,
    collect_used_nicks,
    config_has_model_identity,
    config_has_model_identity_record,
    identities_missing_from_config,
    make_unique_nick,
    sync_models_from_stats_db,
)


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")


def test_config_has_model_identity_matches_domain() -> None:
    remote = [
        {
            "name": "google/gemini-3-flash-preview",
            "domain_match": ["openrouter.ai"],
            "nick": "gemini",
        },
    ]
    assert config_has_model_identity(
        remote,
        "google/gemini-3-flash-preview",
        "openrouter.ai",
    )
    assert config_has_model_identity(
        remote,
        "google/gemini-3-flash-preview",
        "api.openrouter.ai",
    )
    assert not config_has_model_identity(
        remote,
        "google/gemini-3-flash-preview",
        "api.anthropic.com",
    )


def test_config_has_model_identity_resolves_provider_nick() -> None:
    remote = [
        {
            "name": "claude-sonnet-4-6",
            "provider_nick": "anthropic",
            "nick": "sonnet",
        },
    ]
    config: dict[str, Any] = {"models": {"providers": []}}
    assert config_has_model_identity(
        remote,
        "claude-sonnet-4-6",
        "api.anthropic.com",
        config=config,
    )
    assert not config_has_model_identity(
        remote,
        "claude-sonnet-4-6",
        "openrouter.ai",
        config=config,
    )


def test_config_has_model_identity_matches_moonshot_preset() -> None:
    config: dict[str, Any] = {
        "models": {
            "providers": [
                {
                    "provider_nick": "openrouter",
                    "provider": "openrouter",
                    "domain_match": ["openrouter.ai", "api.openrouter.ai"],
                },
            ],
            "llm": {
                "remote": [
                    {
                        "name": "@preset/moonshotai-kimi-k2-6-fp4",
                        "nick": "openrouter-moonshotai-kimi-k2-6-fp4-2",
                        "provider_nick": "openrouter",
                    },
                ],
            },
        },
    }
    remote = config["models"]["llm"]["remote"]
    assert config_has_model_identity(
        remote,
        "@preset/moonshotai-kimi-k2-6-fp4",
        "openrouter.ai",
        config=config,
    )


def test_config_has_model_identity_matches_broken_nick_prefix() -> None:
    config: dict[str, Any] = {
        "models": {
            "providers": [
                {
                    "provider_nick": "openrouter",
                    "domain_match": ["openrouter.ai"],
                },
            ],
            "llm": {
                "remote": [
                    {
                        "name": "@preset/moonshotai-kimi-k2-6-fp4",
                        "nick": "openrouter-moonshotai-kimi-k2-6-fp4-3",
                    },
                ],
            },
        },
    }
    remote = config["models"]["llm"]["remote"]
    assert config_has_model_identity(
        remote,
        "@preset/moonshotai-kimi-k2-6-fp4",
        "openrouter.ai",
        config=config,
    )


def test_config_has_model_identity_matches_provider_without_dns() -> None:
    config: dict[str, Any] = {
        "models": {
            "providers": [{"provider_nick": "deepinfra", "domain_match": ["deepinfra.com"]}],
            "rerankers": {
                "remote": [
                    {
                        "name": "deepinfra/Qwen/Qwen3-Reranker-8B",
                        "nick": "rerank-qwen3-8b",
                        "provider_nick": "deepinfra",
                    },
                ],
            },
        },
    }
    remote = config["models"]["rerankers"]["remote"]
    assert config_has_model_identity_record(
        remote,
        ModelIdentity("rerank", "deepinfra/Qwen/Qwen3-Reranker-8B", None, "deepinfra"),
        config=config,
    )
    assert config_has_model_identity_record(
        remote,
        ModelIdentity("rerank", "deepinfra/Qwen/Qwen3-Reranker-8B", "", "deepinfra"),
        config=config,
    )


def test_base_nick_uses_second_level_domain_not_subdomain() -> None:
    nick = _base_nick(
        "google/gemini-3-flash-preview",
        provider=None,
        provider_dns_name="api.openrouter.ai",
    )
    assert nick == "openrouter-gemini-3-flash-preview"
    assert not nick.startswith("api-")


def test_make_unique_nick_avoids_collisions() -> None:
    used = {"mercury-2", "openrouter-inception-mercury-2"}
    before = set(used)
    nick = make_unique_nick(
        "inception/mercury-2",
        provider="openrouter",
        provider_dns_name="openrouter.ai",
        used=used,
    )
    assert nick not in before
    assert nick == "openrouter-inception-mercury-2-2"


def test_build_model_entry_omits_missing_provider() -> None:
    entry = build_model_entry(
        ModelIdentity(
            stage="llm",
            model_name="unknown/model",
            provider_dns_name="api.example.com",
            provider=None,
        ),
        "example-unknown-model",
    )
    assert entry["name"] == "unknown/model"
    assert entry["provider_nick"] == "api-example-com"
    assert entry["domain_match"] == ["api.example.com"]


def test_build_model_entry_uses_provider_nick_for_known_provider() -> None:
    entry = build_model_entry(
        ModelIdentity(
            stage="rerank",
            model_name="Qwen/Qwen3-Reranker-8B",
            provider_dns_name="api.deepinfra.com",
            provider="deepinfra",
        ),
        "rerank-qwen3-8b",
        config={"models": {"providers": []}},
    )
    assert entry["provider_nick"] == "deepinfra"
    assert entry["domain_match"] == ["api.deepinfra.com"]


def test_sync_skips_model_already_mapped_via_provider_nick() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = str(tmp_path / "stats.db")
        config_path = tmp_path / "config.yaml"

        _write_config(
            config_path,
            {
                "models": {
                    "llm": {
                        "remote": [
                            {
                                "name": "claude-sonnet-4-6",
                                "nick": "sonnet",
                                "provider_nick": "anthropic",
                            },
                        ],
                    },
                },
            },
        )

        db = StatsDB.init(db_path)
        try:
            db.record_proxy_request(
                ProxyRequestRecord(
                    endpoint="anthropic",
                    tools_in=100,
                    tool_count_in=1,
                    tool_properties_count_in=1,
                    tools_out=50,
                    tool_count_out=1,
                    tool_properties_count_out=1,
                    prune_status="applied",
                    pipeline=["llm"],
                    upstream_model_name="claude-sonnet-4-6",
                    upstream_provider_dns="api.anthropic.com",
                ),
            )
        finally:
            db.close()

        assert sync_models_from_stats_db(db_path, config_path) == []
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert len(loaded["models"]["llm"]["remote"]) == 1


def test_sync_appends_only_missing_models() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = str(tmp_path / "stats.db")
        config_path = tmp_path / "config.yaml"

        _write_config(
            config_path,
            {
                "models": {
                    "llm": {
                        "remote": [
                            {
                                "name": "google/gemini-3-flash-preview",
                                "nick": "gemini",
                                "domain_match": ["openrouter.ai"],
                            },
                        ],
                    },
                },
            },
        )

        db = StatsDB.init(db_path)
        try:
            db.record_proxy_request(
                ProxyRequestRecord(
                    endpoint="anthropic",
                    tools_in=100,
                    tool_count_in=1,
                    tool_properties_count_in=1,
                    tools_out=50,
                    tool_count_out=1,
                    tool_properties_count_out=1,
                    prune_status="applied",
                    pipeline=["rerank", "llm"],
                    upstream_model_name="google/gemini-3-flash-preview",
                    upstream_provider_dns="openrouter.ai",
                    pruning_stages={
                        "rerank": StageTokenUsage(
                            input_tokens=10,
                            output_tokens=1,
                            model_name="Qwen/Qwen3-Reranker-8B",
                            provider_dns_name="api.deepinfra.com",
                            provider="deepinfra",
                        ),
                        "llm": StageTokenUsage(
                            input_tokens=20,
                            output_tokens=2,
                            model_name="new/pruner-model",
                            provider_dns_name="api.new.com",
                        ),
                    },
                ),
            )
        finally:
            db.close()

        lines = sync_models_from_stats_db(db_path, config_path)
        assert len(lines) == 2
        assert any("rerankers" in line for line in lines)
        assert any("llm" in line and "new/pruner-model" in line for line in lines)

        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        llm_names = [e["name"] for e in loaded["models"]["llm"]["remote"]]
        rerank_names = [e["name"] for e in loaded["models"]["rerankers"]["remote"]]
        assert "google/gemini-3-flash-preview" in llm_names
        assert "new/pruner-model" in llm_names
        assert "Qwen/Qwen3-Reranker-8B" in rerank_names

        nicks = collect_used_nicks(loaded)
        assert len(nicks) == len(set(nicks))

        assert sync_models_from_stats_db(db_path, config_path) == []


def test_identities_missing_from_config_dedupes_stage() -> None:
    config: dict[str, Any] = {"models": {"llm": {"remote": []}, "rerankers": {"remote": []}}}
    identities = [
        ModelIdentity("llm", "a", "dns", None),
        ModelIdentity("upstream", "a", "dns", None),
    ]
    missing = identities_missing_from_config(identities, config)
    assert len(missing) == 1
    assert missing[0][0] == "llm"


def test_build_models_overlay_splits_kinds() -> None:
    overlay = build_models_overlay(
        [
            ("llm", ModelIdentity("llm", "m1", "d1", "p1")),
            ("rerankers", ModelIdentity("rerank", "m2", "d2", None)),
        ],
        used_nicks=set(),
        config={"models": {"providers": []}},
    )
    assert len(overlay["models"]["llm"]["remote"]) == 1
    assert len(overlay["models"]["rerankers"]["remote"]) == 1

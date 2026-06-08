#!/usr/bin/env python3
"""Tests for config path resolution and loading."""

from __future__ import annotations

from pathlib import Path

import pytest

import cyt.config as configs


@pytest.fixture
def isolated_config_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    user_config = tmp_path / "home" / ".configs" / "cyt" / "config.yaml"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(configs, "DEFAULT_USER_CONFIG_PATH", user_config)
    monkeypatch.chdir(work_dir)
    return {
        "root": tmp_path,
        "work": work_dir,
        "user_config": user_config,
        "cwd_config": tmp_path / "work" / "config.yaml",
    }


def test_resolve_config_path_explicit(isolated_config_paths: dict[str, Path]) -> None:
    explicit = isolated_config_paths["root"] / "custom.yaml"
    assert configs.resolve_config_path(explicit) == explicit


def test_resolve_config_path_prefers_cwd(isolated_config_paths: dict[str, Path]) -> None:
    cwd_config = isolated_config_paths["cwd_config"]
    cwd_config.write_text("defaults:\n  is_persistent: false\n", encoding="utf-8")
    assert configs.resolve_config_path(None) == cwd_config


def test_resolve_config_path_falls_back_to_user_config(
    isolated_config_paths: dict[str, Path],
) -> None:
    assert configs.resolve_config_path(None) == isolated_config_paths["user_config"]


def test_resolve_setup_config_path_defaults_to_user_config(
    isolated_config_paths: dict[str, Path],
) -> None:
    assert configs.resolve_setup_config_path(None) == isolated_config_paths["user_config"]


def test_resolve_setup_config_path_explicit(
    isolated_config_paths: dict[str, Path],
) -> None:
    explicit = isolated_config_paths["root"] / "setup.yaml"
    assert configs.resolve_setup_config_path(explicit) == explicit


def test_load_config_creates_user_config_when_missing(
    isolated_config_paths: dict[str, Path],
) -> None:
    user_config = isolated_config_paths["user_config"]
    assert not user_config.exists()

    loaded = configs.load_config()

    assert user_config.exists()
    assert loaded["network"]["proxy"]["reverse"]["port"] == 8834
    assert loaded["stats"]["database"]["path"] == configs.DEFAULT_STATS_DB_PATH
    written = configs._load_yaml_dict(user_config)
    ssl = written["network"]["proxy"]["reverse"]["http2"]["ssl"]
    assert ssl["keyfile"] == "~/.config/cyt/crt/key.pem"
    assert written["pruning"]["per_tool"] == {}


def test_load_config_uses_cwd_config(isolated_config_paths: dict[str, Path]) -> None:
    cwd_config = isolated_config_paths["cwd_config"]
    cwd_config.write_text(
        "network:\n  proxy:\n    reverse:\n      port: 9999\n",
        encoding="utf-8",
    )

    loaded = configs.load_config()

    assert loaded["network"]["proxy"]["reverse"]["port"] == 9999
    assert not isolated_config_paths["user_config"].exists()


def test_load_config_explicit_missing_returns_defaults(
    isolated_config_paths: dict[str, Path],
) -> None:
    missing = isolated_config_paths["root"] / "missing.yaml"
    loaded = configs.load_config(missing)

    assert loaded["network"]["proxy"]["reverse"]["port"] == 8834
    assert configs.pruning_stage_model_nick(loaded, "rerank") == "rerank-qwen3-8b"
    assert not missing.exists()
    assert not isolated_config_paths["user_config"].exists()


def test_load_config_layers_bundled_defaults_under_user_overrides(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled = isolated_config_paths["root"] / "bundled.yaml"
    bundled.write_text(
        "pruning:\n  rerank:\n    model:\n      remote:\n        model_nick: bundled-rerank\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        configs,
        "_load_bundled_defaults_yaml",
        lambda: configs._load_yaml_dict(bundled),
    )

    user_config = isolated_config_paths["user_config"]
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text("defaults:\n  is_persistent: false\n", encoding="utf-8")

    loaded = configs.load_config()

    assert loaded["defaults"]["is_persistent"] is False
    assert configs.pruning_stage_model_nick(loaded, "rerank") == "bundled-rerank"


def test_required_proxy_env_var_names_excludes_upstream_keys(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["network"]["proxy"]["reverse"]["upstreams"] = [
        {
            "upstream": "openai",
            "url": "https://api.openai.com",
            "kind": "openai",
        },
    ]
    config["network"]["proxy"]["reverse"]["endpoints"] = ["openai"]

    required = configs.required_proxy_env_var_names(config)

    assert "OPENAI_API_KEY" not in required
    assert "DEEPINFRA_API_KEY" in required


def test_require_proxy_env_not_needed_for_serve_without_pipeline_keys(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = configs.load_config()
    config["pruning"]["pipeline"] = ["bm25"]
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    configs.require_proxy_env(config)


def test_missing_proxy_env_var_names(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = configs.load_config()
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

    missing = configs.missing_proxy_env_var_names(config)

    assert "DEEPINFRA_API_KEY" in missing


def test_format_proxy_env_help_lists_alternatives() -> None:
    message = configs.format_proxy_env_help(["DEEPINFRA_API_KEY", "OPENAI_API_KEY"])

    assert "\tDEEPINFRA_API_KEY" in message
    assert "\tOPENAI_API_KEY" in message
    assert f"\t{configs.CWD_ENV_PATH}" in message
    assert f"\t{configs.USER_ENV_PATH}" in message
    assert "cyt proxy --upstream" in message
    assert "cyt setup" in message


def test_require_proxy_env_raises_with_help_text(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = configs.load_config()
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="cyt setup"):
        configs.require_proxy_env(config)


def test_remote_pruning_pipeline_configured_requires_user_models() -> None:
    assert configs.remote_pruning_pipeline_configured({}) is False
    assert configs.remote_pruning_pipeline_configured({"pruning": {"pipeline": ["bm25"]}}) is False
    assert (
        configs.remote_pruning_pipeline_configured({"pruning": {"pipeline": ["rerank"]}}) is False
    )

    configured = {
        "pruning": {"pipeline": ["rerank"]},
        "defaults": {"remote": {"reranking_model_nick": "rerank-qwen3-8b"}},
        "models": {
            "rerankers": {
                "remote": [{"nick": "rerank-qwen3-8b", "key_var_name": "DEEPINFRA_API_KEY"}],
            },
        },
    }
    assert configs.remote_pruning_pipeline_configured(configured) is True


def test_load_user_config_overlay_reads_on_disk_only(
    isolated_config_paths: dict[str, Path],
) -> None:
    cwd_config = isolated_config_paths["cwd_config"]
    cwd_config.write_text("network:\n  proxy:\n    reverse:\n      port: 7777\n", encoding="utf-8")

    overlay = configs.load_user_config_overlay()

    assert overlay["network"]["proxy"]["reverse"]["port"] == 7777
    assert "defaults" not in overlay


def test_pruning_stage_model_nick_prefers_new_path() -> None:
    config = {
        "pruning": {
            "rerank": {"model": {"remote": {"model_nick": "new-rerank"}}},
        },
        "defaults": {"remote": {"reranking_model_nick": "legacy-rerank"}},
    }
    assert configs.pruning_stage_model_nick(config, "rerank") == "new-rerank"


def test_pruning_stage_model_nick_legacy_fallback() -> None:
    config = {"defaults": {"remote": {"reranking_model_nick": "legacy-rerank"}}}
    assert configs.pruning_stage_model_nick(config, "rerank") == "legacy-rerank"


def test_effective_output_policy_stage_override() -> None:
    config = {
        "pruning": {
            "policy": {"system_tool": "prune_optional", "mcp_tool": "prune_all"},
            "bm25": {
                "policy": {
                    "system_tool": "prune_optional_descriptions",
                    "mcp_tool": "prune_all_descriptions",
                },
            },
        },
    }
    assert (
        configs.effective_output_policy(
            config,
            "mcp__srv__tool",
            terminal_stage="bm25",
        )
        == "prune_all_descriptions"
    )
    assert (
        configs.effective_output_policy(
            config,
            "Agent",
            terminal_stage="bm25",
        )
        == "prune_optional_descriptions"
    )
    assert (
        configs.effective_output_policy(
            config,
            "mcp__srv__tool",
            terminal_stage="rerank",
        )
        == "prune_all"
    )


def test_pruning_policy_prefers_new_path() -> None:
    config = {
        "pruning": {"policy": {"system_tool": "always_include", "mcp_tool": "prune_optional"}},
        "defaults": {
            "system_tool_policy": "prune_all",
            "mcp_tool_policy": "prune_all",
        },
    }
    assert configs.pruning_system_tool_policy(config) == "always_include"
    assert configs.pruning_mcp_tool_policy(config) == "prune_optional"


def test_minimum_tools_shared_across_stages() -> None:
    config = {"pruning": {"policy": {"minimum_tools": 30}}}
    assert configs.llm_minimum_tools(config) == 30
    assert configs.reranker_minimum_tools(config) == 30


def test_minimum_tools_legacy_models_fallback() -> None:
    config = {"models": {"rerankers": {"minimum_tools": 40}, "llm": {"minimum_tools": 40}}}
    assert configs.llm_minimum_tools(config) == 40
    assert configs.reranker_minimum_tools(config) == 40


def test_bm25_index_dir_prefers_pruning_path() -> None:
    config = {
        "pruning": {"bm25": {"index_dir": "/tmp/pruning-bm25"}},
        "models": {"bm25": {"index_dir": "/tmp/models-bm25", "mmap": False}},
    }
    assert str(configs.bm25_index_dir(config)) == "/tmp/pruning-bm25"
    assert configs.bm25_mmap_enabled(config) is False


def test_litellm_model_name_uses_chat_completions_by_default() -> None:
    entry = {
        "provider": "openai",
        "name": "gpt-5.5",
        "nick": "gpt-5.5",
    }
    assert configs.litellm_model_name(entry) == "openai/gpt-5.5"


def test_model_responses_api_defaults_to_false() -> None:
    entry = {
        "provider": "openai",
        "name": "gpt-5.5",
        "nick": "gpt-5.5",
    }
    assert configs.model_responses_api(entry) is False


def test_model_responses_api_reads_entry_flag() -> None:
    entry = {
        "provider": "openai",
        "name": "gpt-5.5",
        "nick": "gpt-5.5",
        "responses_api": True,
    }
    assert configs.model_responses_api(entry) is True


def test_remote_pruning_pipeline_configured_accepts_new_paths() -> None:
    configured = {
        "pruning": {
            "pipeline": ["rerank"],
            "rerank": {"model": {"remote": {"model_nick": "rerank-qwen3-8b"}}},
        },
        "models": {
            "rerankers": {
                "remote": [{"nick": "rerank-qwen3-8b", "key_var_name": "DEEPINFRA_API_KEY"}],
            },
        },
    }
    assert configs.remote_pruning_pipeline_configured(configured) is True

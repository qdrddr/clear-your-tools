#!/usr/bin/env python3
"""Tests for config path resolution and loading."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import cyt.config as configs


@pytest.fixture(autouse=True)
def _reset_bundled_defaults_cache() -> Iterator[None]:
    configs.clear_bundled_defaults_cache()
    yield
    configs.clear_bundled_defaults_cache()


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


def test_save_user_config_skips_unchanged_write(
    isolated_config_paths: dict[str, Path],
) -> None:
    user_config = isolated_config_paths["user_config"]
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text("defaults:\n  is_persistent: true\n", encoding="utf-8")
    original_bytes = user_config.read_bytes()

    written = configs.save_user_config(
        user_config,
        {"defaults": {"is_persistent": True}},
        apply_bundled_sections=False,
    )
    assert written is False
    assert user_config.read_bytes() == original_bytes

    written = configs.save_user_config(
        user_config,
        {"defaults": {"is_persistent": False}},
        apply_bundled_sections=False,
    )
    assert written is True
    assert configs._load_yaml_dict(user_config)["defaults"]["is_persistent"] is False


def test_load_config_creates_user_config_when_missing(
    isolated_config_paths: dict[str, Path],
) -> None:
    user_config = isolated_config_paths["user_config"]
    assert not user_config.exists()

    loaded = configs.load_config()

    assert user_config.exists()
    assert loaded["network"]["proxy"]["reverse"]["port"] == 8834
    bundled = configs.load_bundled_defaults_yaml()
    assert loaded["stats"]["database"]["path"] == bundled["stats"]["database"]["path"]
    written = configs._load_yaml_dict(user_config)
    ssl = written["network"]["proxy"]["reverse"]["http2"]["ssl"]
    assert ssl["keyfile"] == "~/.config/cyt/crt/key.pem"
    per_tool = written.get("pruning", {}).get("tools", {}).get("policy", {}).get("per_tool")
    assert per_tool == {}


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
        "pruning:\n  tools:\n    pipelines:\n      rerank:\n        model_nick: bundled-rerank\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        configs,
        "_load_bundled_defaults_yaml",
        lambda: configs._load_yaml_dict(bundled),
    )
    configs.clear_bundled_defaults_cache()

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
            "endpoint": "openai",
            "url": "https://api.openai.com",
            "kind": "openai",
        },
    ]
    config["network"]["proxy"]["reverse"]["endpoints"] = ["openai"]
    config["pruning"]["tools"]["sequence"] = ["bm25", "rerank"]

    required = configs.required_proxy_env_var_names(config)

    assert "OPENAI_API_KEY" not in required
    assert "DEEPINFRA_API_KEY" in required


def test_require_proxy_env_not_needed_for_serve_without_pipeline_keys(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = configs.load_config()
    config["pruning"]["tools"]["sequence"] = ["bm25"]
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    configs.require_proxy_env(config)


def test_missing_proxy_env_var_names(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = configs.load_config()
    config["pruning"]["tools"]["sequence"] = ["bm25", "rerank"]
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

    missing = configs.missing_proxy_env_var_names(config)

    assert "DEEPINFRA_API_KEY" in missing


def test_required_proxy_env_var_names_includes_skills_pipeline_keys(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["tools"]["sequence"] = ["bm25"]
    config["skills"] = {"enabled": True, "pipeline": "rerank"}

    required = configs.required_proxy_env_var_names(config)

    assert required == ["DEEPINFRA_API_KEY"]


def test_required_proxy_env_var_names_skips_skills_bm25(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["tools"]["sequence"] = ["bm25"]
    config["skills"] = {"enabled": True, "pipeline": "bm25"}

    assert configs.required_proxy_env_var_names(config) == []
    assert configs.required_skills_env_var_names(config) == []


def test_required_skills_env_var_names_llm_pipeline(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["skills"] = {"enabled": True, "pipeline": "llm"}

    assert configs.required_skills_env_var_names(config) == ["OPENROUTER_API_KEY"]


def test_required_pruning_env_var_names_llm_pipeline(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["tools"]["sequence"] = ["llm"]

    assert configs.required_pruning_env_var_names(config) == ["OPENROUTER_API_KEY"]


def test_required_pruning_env_var_names_bm25_only(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["tools"]["sequence"] = ["bm25"]

    assert configs.required_pruning_env_var_names(config) == []


def test_required_tools_hook_env_var_names_executor_mode(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["pruning"]["tools"]["hook"]["tools_from"] = "executor"

    assert configs.required_tools_hook_env_var_names(config) == ["EXECUTOR_TOKEN"]


def test_required_tools_hook_env_var_names_skipped_when_tools_disabled(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["pruning"]["tools"]["enabled"] = False
    config["pruning"]["tools"]["hook"]["tools_from"] = "executor"

    assert configs.required_tools_hook_env_var_names(config) == []


def test_required_pruning_env_var_names_skipped_when_tools_disabled(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["tools"]["enabled"] = False
    config["pruning"]["tools"]["sequence"] = ["llm"]

    assert configs.required_pruning_env_var_names(config) == []


def test_required_tools_hook_env_var_names_definitions_mode(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["pruning"]["tools"]["hook"]["tools_from"] = "definitions"

    assert configs.required_tools_hook_env_var_names(config) == []


def test_required_tools_hook_env_var_names_cloudflare_mode(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["pruning"]["tools"]["hook"]["tools_from"] = ["cloudflare"]
    config["pruning"]["tools"]["hook"]["cloudflare_url"] = "https://mcp.example.com"

    assert configs.required_tools_hook_env_var_names(config) == [
        "CF_ACCESS_CLIENT_ID",
        "CF_ACCESS_CLIENT_SECRET",
    ]


def test_required_tools_hook_env_var_names_cloudflare_custom_var_names(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["pruning"]["tools"]["hook"]["tools_from"] = ["cloudflare"]
    config["pruning"]["tools"]["hook"]["cloudflare_access_client_id_var"] = "MY_CF_ID"
    config["pruning"]["tools"]["hook"]["cloudflare_access_client_secret_var"] = (
        "MY_CF_SECRET"  # pragma: allowlist secret
    )

    assert configs.required_tools_hook_env_var_names(config) == ["MY_CF_ID", "MY_CF_SECRET"]


def test_required_tools_hook_env_var_names_executor_and_cloudflare_dedupes(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["pruning"]["tools"]["hook"]["tools_from"] = ["executor", "cloudflare"]
    config["pruning"]["tools"]["hook"]["executor_token_var"] = "SHARED_TOKEN"
    config["pruning"]["tools"]["hook"]["cloudflare_access_client_id_var"] = "SHARED_TOKEN"
    config["pruning"]["tools"]["hook"]["cloudflare_access_client_secret_var"] = (
        "CF_SECRET"  # pragma: allowlist secret
    )

    assert configs.required_tools_hook_env_var_names(config) == [
        "SHARED_TOKEN",
        "CF_SECRET",
    ]


def test_tools_hook_cloudflare_helpers(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    assert configs.tools_hook_cloudflare_url(config) == ""
    assert configs.tools_hook_cloudflare_access_client_id_var(config) == "CF_ACCESS_CLIENT_ID"
    assert (
        configs.tools_hook_cloudflare_access_client_secret_var(config) == "CF_ACCESS_CLIENT_SECRET"
    )
    assert configs.tools_hook_cloudflare_configured(config) is False
    assert configs.uses_cloudflare_tool_catalog(config) is False

    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["pruning"]["tools"]["hook"]["tools_from"] = ["cloudflare"]
    config["pruning"]["tools"]["hook"]["cloudflare_url"] = "https://mcp.example.com/mcp/"
    assert configs.tools_hook_cloudflare_url(config) == "https://mcp.example.com/mcp"
    assert configs.tools_hook_cloudflare_configured(config) is True
    assert configs.uses_cloudflare_tool_catalog(config) is True


def test_tools_hook_cloudflare_source_usable_without_credentials(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["pruning"]["tools"]["hook"]["tools_from"] = ["cloudflare"]
    config["pruning"]["tools"]["hook"]["cloudflare_url"] = "https://mcp.example.com"

    assert configs.tools_hook_file_missing(config) is False

    config["pruning"]["tools"]["hook"]["cloudflare_url"] = ""
    assert configs.tools_hook_file_missing(config) is True


def test_tools_hook_tools_from_accepts_legacy_client_alias(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["tools"]["hook"]["tools_from"] = "client"

    assert configs.tools_hook_tools_from(config) == "executor"


def test_tools_hook_sources_parses_scalar_and_list(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["pruning"]["tools"]["enabled"] = True
    config["pruning"]["tools"]["hook"]["tools_from"] = "mcpc"
    assert configs.tools_hook_sources(config) == ("mcpc",)

    config["pruning"]["tools"]["hook"]["tools_from"] = ["mcpc", "executor", "mcpc"]
    assert configs.tools_hook_sources(config) == ("mcpc", "executor")

    assert configs.uses_mcpc_tool_catalog(config) is True
    assert configs.uses_executor_tool_catalog(config) is True
    assert configs.uses_definitions_tool_catalog(config) is False


def test_tools_hook_sources_invalid_explicit_value_falls_back_to_executor(
    isolated_config_paths: dict[str, Path],
) -> None:
    config = configs.load_config()
    config["pruning"]["tools"]["hook"]["tools_from"] = "not-a-source"
    assert configs.tools_hook_sources(config) == ("executor",)


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
    config["pruning"]["tools"]["sequence"] = ["bm25", "rerank"]
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="cyt setup"):
        configs.require_proxy_env(config)


def test_remote_pruning_pipeline_configured_requires_user_models() -> None:
    assert configs.remote_pruning_pipeline_configured({}) is False
    assert (
        configs.remote_pruning_pipeline_configured({"pruning": {"tools": {"sequence": ["bm25"]}}})
        is False
    )
    assert (
        configs.remote_pruning_pipeline_configured({"pruning": {"tools": {"sequence": ["rerank"]}}})
        is False
    )

    configured = {
        "pruning": {
            "tools": {
                "sequence": ["rerank"],
                "pipelines": {"rerank": {"model_nick": "rerank-qwen3-8b"}},
            },
        },
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


def test_pruning_stage_model_nick_reads_canonical_path() -> None:
    config = {
        "pruning": {
            "tools": {
                "pipelines": {
                    "rerank": {"model_nick": "new-rerank"},
                },
            },
        },
    }
    assert configs.pruning_stage_model_nick(config, "rerank") == "new-rerank"


def test_effective_output_policy_stage_override() -> None:
    config = {
        "pruning": {
            "tools": {
                "policy": {"system_tool": "prune_optional", "mcp_tool": "prune_all"},
                "pipelines": {
                    "bm25": {
                        "policy": {
                            "system_tool": "prune_optional_descriptions",
                            "mcp_tool": "prune_all_descriptions",
                        },
                    },
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


def test_pruning_policy_reads_canonical_path() -> None:
    config = {
        "pruning": {
            "tools": {
                "policy": {
                    "system_tool": "always_include",
                    "mcp_tool": "prune_optional",
                },
            },
        },
    }
    assert configs.pruning_system_tool_policy(config) == "always_include"
    assert configs.pruning_mcp_tool_policy(config) == "prune_optional"


def test_minimum_tools_shared_across_stages() -> None:
    config = {"pruning": {"tools": {"policy": {"minimum_tools": 30}}}}
    assert configs.llm_minimum_tools(config) == 30
    assert configs.reranker_minimum_tools(config) == 30


def test_selector_soft_budget_reads_config() -> None:
    config = {
        "pruning": {"tools": {"selector_soft_budget": 3500}},
        "skills": {"selector_soft_budget": 1800},
    }
    assert configs.tools_selector_soft_budget(config) == 3500
    assert configs.skills_selector_soft_budget(config) == 1800


def test_selector_soft_budget_defaults() -> None:
    bundled = configs.load_bundled_defaults_yaml()
    assert configs.tools_selector_soft_budget({}) == bundled["pruning"]["tools"]["selector_soft_budget"]
    assert configs.skills_selector_soft_budget({}) == bundled["skills"]["selector_soft_budget"]


def test_bm25_index_dir_reads_canonical_path() -> None:
    config = {
        "pruning": {
            "tools": {
                "pipelines": {
                    "bm25": {"index_dir": "/tmp/pruning-bm25"},
                },
            },
        },
        "models": {"bm25": {"index_dir": "/tmp/models-bm25", "mmap": False}},
    }
    assert configs.bm25_index_dir(config).as_posix() == "/tmp/pruning-bm25"
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


def test_remote_pruning_pipeline_configured_accepts_canonical_paths() -> None:
    configured = {
        "pruning": {
            "tools": {
                "sequence": ["rerank"],
                "pipelines": {"rerank": {"model_nick": "rerank-qwen3-8b"}},
            },
        },
        "models": {
            "rerankers": {
                "remote": [{"nick": "rerank-qwen3-8b", "key_var_name": "DEEPINFRA_API_KEY"}],
            },
        },
    }
    assert configs.remote_pruning_pipeline_configured(configured) is True


def test_bundled_defaults_is_sole_base(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled = isolated_config_paths["root"] / "bundled.yaml"
    bundled.write_text(
        "pruning:\n  max_batch_workers: 17\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        configs,
        "_load_bundled_defaults_yaml",
        lambda: configs._load_yaml_dict(bundled),
    )
    configs.clear_bundled_defaults_cache()

    missing = isolated_config_paths["root"] / "missing.yaml"
    loaded = configs.load_config(missing)
    merged = configs._merged_config({})

    assert loaded["pruning"]["max_batch_workers"] == 17
    assert merged["pruning"]["max_batch_workers"] == 17
    assert configs.max_prune_batch_workers({}) == 17


def test_bundled_defaults_cache_invalidation(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled_a = isolated_config_paths["root"] / "bundled-a.yaml"
    bundled_b = isolated_config_paths["root"] / "bundled-b.yaml"
    bundled_a.write_text("pruning:\n  max_batch_workers: 3\n", encoding="utf-8")
    bundled_b.write_text("pruning:\n  max_batch_workers: 9\n", encoding="utf-8")

    monkeypatch.setattr(
        configs,
        "_load_bundled_defaults_yaml",
        lambda: configs._load_yaml_dict(bundled_a),
    )
    configs.clear_bundled_defaults_cache()
    assert configs.max_prune_batch_workers({}) == 3

    monkeypatch.setattr(
        configs,
        "_load_bundled_defaults_yaml",
        lambda: configs._load_yaml_dict(bundled_b),
    )
    configs.clear_bundled_defaults_cache()
    assert configs.max_prune_batch_workers({}) == 9


@pytest.mark.parametrize(
    "path",
    [
        ("defaults", "is_persistent"),
        ("pruning", "inject_via_default"),
        ("pruning", "max_batch_workers"),
        ("pruning", "tools", "enabled"),
        ("pruning", "tools", "sequence"),
        ("pruning", "tools", "hook", "tools_from"),
        ("models", "bm25", "mmap"),
        ("cache", "enabled"),
        ("stats", "rollup_on_query"),
        ("hallucination_gate", "enabled"),
        ("skills", "enabled"),
        ("network", "proxy", "reverse", "port"),
    ],
)
def test_bundled_defaults_required_paths(path: tuple[str, ...]) -> None:
    node: object = configs.load_bundled_defaults_yaml()
    for key in path:
        assert isinstance(node, dict), f"missing path {'.'.join(path)}"
        assert key in node, f"missing path {'.'.join(path)}"
        node = node[key]


def _yaml_at(bundled: dict, path: tuple[str, ...]) -> object:
    node: object = bundled
    for key in path:
        assert isinstance(node, dict), f"missing path {'.'.join(path)}"
        assert key in node, f"missing path {'.'.join(path)}"
        node = node[key]
    return node


def test_legacy_default_constants_exist_in_bundled_yaml() -> None:
    from tests.unit.legacy_config_default_paths import INJECT_VIA_AGENTS, LEGACY_DEFAULT_PATHS

    bundled = configs.load_bundled_defaults_yaml()
    for name, path in LEGACY_DEFAULT_PATHS.items():
        _yaml_at(bundled, path)
    inject_via = _yaml_at(bundled, ("pruning", "inject_via"))
    assert isinstance(inject_via, dict)
    for agent in INJECT_VIA_AGENTS:
        assert agent in inject_via, f"missing pruning.inject_via.{agent} (was DEFAULT_INJECT_VIA_BY_AGENT)"


def test_legacy_defaults_dict_paths_exist_in_bundled_yaml() -> None:
    """Every key from the removed ``_DEFAULTS`` dict exists in defaults.yaml."""
    import subprocess
    from pathlib import Path
    from typing import Any

    text = subprocess.check_output(
        ["git", "show", "HEAD:src/cyt/config/__init__.py"],
        text=True,
    )
    cut = text.index("_DEFAULTS: dict[str, Any] = {")
    end = cut
    level = 0
    for i, ch in enumerate(text[cut:], cut):
        if ch == "{":
            level += 1
        elif ch == "}":
            level -= 1
            if level == 0:
                end = i + 1
                break
    prelude = text[: text.index("DEFAULT_REVERSE_PORT")] + "ToolPolicy = str\n"
    block = text[text.index("DEFAULT_REVERSE_PORT") : end]
    ns: dict[str, Any] = {"__builtins__": __builtins__, "Any": Any, "Path": Path}
    exec(prelude + block, ns, ns)  # noqa: S102
    old_defaults = ns["_DEFAULTS"]

    def flatten(d: object, prefix: str = "") -> dict[str, object]:
        out: dict[str, object] = {}
        if isinstance(d, dict):
            for k, v in d.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                out.update(flatten(v, key))
        elif isinstance(d, list):
            out[prefix] = d
        else:
            out[prefix] = d
        return out

    bundled = configs.load_bundled_defaults_yaml()
    for dot_path in flatten(old_defaults):
        parts = tuple(dot_path.split("."))
        _yaml_at(bundled, parts)

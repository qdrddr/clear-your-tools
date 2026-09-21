"""Tests for tools hook setup wizard helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.config import load_config
from cyt.testing.inject_via_maps import apply_inject_via_overlay
from cyt.tools import hook_setup
from cyt.tools.hook_setup import prompt_tools_hook_config


def test_prompt_tools_hook_config_preserves_multi_source_list_when_not_hook_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    apply_inject_via_overlay(
        config,
        {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
    )
    config["tools"]["hook"]["tools_from"] = ["mcpc", "executor"]

    overlay = prompt_tools_hook_config(config, context="setup", inject_mode="proxy")

    assert overlay["hook"]["tools_from"] == ["mcpc", "executor"]


def test_prompt_tools_hook_config_preserves_single_source_as_list_when_not_hook_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    apply_inject_via_overlay(
        config,
        {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
    )
    config["tools"]["hook"]["tools_from"] = ["mcpc"]

    overlay = prompt_tools_hook_config(config, context="setup", inject_mode="proxy")

    assert overlay["hook"]["tools_from"] == ["mcpc"]


def test_prompt_tools_hook_config_saves_single_source_as_list_in_hook_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    config["tools"]["hook"]["tools_from"] = ["mcpc"]
    monkeypatch.setattr(hook_setup, "_prompt", lambda _label, default: default)

    overlay = prompt_tools_hook_config(config, context="hook")

    assert overlay["hook"]["tools_from"] == ["mcpc"]


def test_prompt_tools_hook_config_prompts_cloudflare_url_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    prompts: list[tuple[str, str]] = []

    def fake_prompt(label: str, default: str) -> str:
        prompts.append((label, default))
        if label == "Tool catalog sources":
            return "cloudflare"
        if label == "Cloudflare MCP portal URL":
            return "https://mcp.example.com/mcp"
        return default

    monkeypatch.setattr(hook_setup, "_prompt", fake_prompt)

    overlay = prompt_tools_hook_config(config, context="hook")

    assert overlay["hook"]["tools_from"] == ["cloudflare"]
    assert overlay["hook"]["cloudflare_url"] == "https://mcp.example.com/mcp"
    assert any(label == "Cloudflare MCP portal URL" for label, _default in prompts)


def test_ensure_tools_hook_file_interactive_prompts_for_missing_cloudflare_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from cyt.tools.hook_setup import ensure_tools_hook_file_interactive

    config_path = tmp_path / "config.yaml"
    config = load_config()
    apply_inject_via_overlay(
        config,
        {"cursor": "hook", "claude": "hook", "codex": "hook"},
    )
    config["tools"]["hook"]["tools_from"] = ["cloudflare"]
    config["tools"]["hook"]["cloudflare_url"] = ""

    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(hook_setup, "_prompt_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        hook_setup,
        "prompt_tools_hook_config",
        lambda *_args, **_kwargs: {"hook": {"cloudflare_url": "https://mcp.example.com"}},
    )
    monkeypatch.setattr(hook_setup, "save_user_config", lambda *_args, **_kwargs: False)

    ensure_tools_hook_file_interactive(config_path, config)


def test_prompt_tools_hook_config_clears_stale_verify_only_in_aggregator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cyt.tools.cyt_mcp_setup as cyt_mcp_setup

    aggregator_path = tmp_path / "mcp-aggregator.yaml"
    mcp_dir = tmp_path / "cyt_mcp"
    aggregator_path.write_text(
        "\n".join(
            [
                "default_agent: cursor",
                "transport: stdio",
                "verify_only: true",
                "",
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_CONFIG_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", mcp_dir)
    monkeypatch.setattr(hook_setup, "_prompt", lambda _label, default: default)
    monkeypatch.setattr(hook_setup, "_prompt_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        cyt_mcp_setup,
        "prompt_cyt_mcp_transport",
        lambda **kwargs: "stdio",
    )

    config = load_config()
    prompt_tools_hook_config(config, context="hook", agent="cursor")

    text = aggregator_path.read_text(encoding="utf-8")
    assert "verify_only: false" in text
    assert "verify_only: true" not in text


def test_prompt_tools_hook_config_prompts_user_and_workspace_migration_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import patch

    import cyt.tools.cyt_mcp_setup as cyt_mcp_setup
    from cyt.hook.install_scope import CytInstallScope

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / ".git").mkdir()
    scope = CytInstallScope(workspace_root=consumer.resolve())
    yes_no_calls: list[str] = []

    def capture_yes_no(text: str, *args: object, **kwargs: object) -> bool:
        yes_no_calls.append(text)
        return True

    monkeypatch.setattr(hook_setup, "_prompt", lambda _label, default: "cyt_mcp")
    monkeypatch.setattr(hook_setup, "_prompt_yes_no", capture_yes_no)
    monkeypatch.setattr(
        cyt_mcp_setup,
        "prompt_cyt_mcp_transport",
        lambda **kwargs: "stdio",
    )
    monkeypatch.setattr(
        cyt_mcp_setup,
        "has_migratable_mcp_backends",
        lambda *_args, **_kwargs: True,
    )

    config = load_config()
    apply_inject_via_overlay(
        config,
        {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
    )

    with patch("cyt.tools.cyt_mcp_setup.setup_cyt_mcp_for_agent") as setup_cyt_mcp:
        prompt_tools_hook_config(
            config,
            context="hook",
            agent="cursor",
            install_scope=scope,
        )

    assert any(
        "Migrate user-global MCP backends and install cyt-mcp-usr?" in text
        for text in yes_no_calls
    )
    assert any(
        "Migrate project MCP backends and install cyt-mcp-ws?" in text for text in yes_no_calls
    )
    setup_cyt_mcp.assert_called_once()
    setup_kwargs = setup_cyt_mcp.call_args.kwargs
    assert setup_kwargs["configure_user"] is True
    assert setup_kwargs["configure_workspace"] is True
    assert setup_kwargs["migrate_user_backends"] is True
    assert setup_kwargs["migrate_workspace_backends"] is True
    assert setup_kwargs["require_user_backends"] is False
    assert setup_kwargs["require_workspace_backends"] is False

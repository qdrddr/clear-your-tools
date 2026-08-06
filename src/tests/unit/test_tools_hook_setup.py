"""Tests for tools hook setup wizard helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.config import load_config
from cyt.tools import hook_setup
from cyt.tools.hook_setup import prompt_tools_hook_config


def test_prompt_tools_hook_config_preserves_multi_source_list_when_not_hook_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "proxy", "codex": "proxy"}
    config["pruning"]["tools"]["hook"]["tools_from"] = ["mcpc", "executor"]

    overlay = prompt_tools_hook_config(config, context="setup", inject_mode="proxy")

    assert overlay["hook"]["tools_from"] == ["mcpc", "executor"]


def test_prompt_tools_hook_config_preserves_single_source_as_list_when_not_hook_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "proxy", "codex": "proxy"}
    config["pruning"]["tools"]["hook"]["tools_from"] = ["mcpc"]

    overlay = prompt_tools_hook_config(config, context="setup", inject_mode="proxy")

    assert overlay["hook"]["tools_from"] == ["mcpc"]


def test_prompt_tools_hook_config_saves_single_source_as_list_in_hook_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    config["pruning"]["tools"]["hook"]["tools_from"] = ["mcpc"]
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
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["pruning"]["tools"]["hook"]["tools_from"] = ["cloudflare"]
    config["pruning"]["tools"]["hook"]["cloudflare_url"] = ""

    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(hook_setup, "_prompt_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        hook_setup,
        "prompt_tools_hook_config",
        lambda *_args, **_kwargs: {"hook": {"cloudflare_url": "https://mcp.example.com"}},
    )
    monkeypatch.setattr(hook_setup, "save_user_config", lambda *_args, **_kwargs: False)

    ensure_tools_hook_file_interactive(config_path, config)

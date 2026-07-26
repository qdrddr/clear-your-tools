"""Tests for tools hook setup wizard helpers."""

from __future__ import annotations

import pytest

from cyt.config import load_config
from cyt.tools import hook_setup
from cyt.tools.hook_setup import prompt_tools_hook_config


def test_prompt_tools_hook_config_preserves_multi_source_list_when_not_hook_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    config["pruning"]["inject_via"] = "proxy"
    config["pruning"]["tools"]["hook"]["tools_from"] = ["mcpc", "executor"]

    overlay = prompt_tools_hook_config(config, context="setup", inject_mode="proxy")

    assert overlay["hook"]["tools_from"] == ["mcpc", "executor"]


def test_prompt_tools_hook_config_preserves_single_source_as_list_when_not_hook_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    config["pruning"]["inject_via"] = "proxy"
    config["pruning"]["tools"]["hook"]["tools_from"] = ["mcpc"]

    overlay = prompt_tools_hook_config(config, context="setup", inject_mode="proxy")

    assert overlay["hook"]["tools_from"] == ["mcpc"]


def test_prompt_tools_hook_config_saves_single_source_as_list_in_hook_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    monkeypatch.setattr(hook_setup, "_prompt", lambda _label, default: default)

    overlay = prompt_tools_hook_config(config, context="hook")

    assert overlay["hook"]["tools_from"] == ["mcpc"]

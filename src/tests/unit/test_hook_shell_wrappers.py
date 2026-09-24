"""Unit tests for Cursor hook shell wrappers (fish/bash compatibility)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cyt.hook import setup_wizard as hook_setup
from cyt.hook.cli_invocation import (
    HookCliInvocation,
    agent_hook_command_env,
    is_hook_shell_wrapper_command,
    is_unix_hook_wrapper_command,
    prefix_agent_hook_command,
    prefix_command_env,
    use_hook_shell_wrappers,
)
from tests.support.cursor_hook_shell_wrapper_fixtures import (
    FISH_BREAKING_INLINE_PREFIX,
    assert_wrapper_command_is_fish_safe,
    cursor_dev_client_wrapper_command,
    dev_repo_root,
    fish_available,
    install_dev_hook_wrappers,
    legacy_fish_breaking_client_command,
    load_legacy_fish_breaking_hooks,
    wrapper_suffix,
)


def test_use_hook_shell_wrappers_is_enabled_for_cursor_hooks() -> None:
    assert use_hook_shell_wrappers() is True


@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper semantics")
def test_cursor_hook_client_command_returns_shell_wrapper_not_inline_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = cursor_dev_client_wrapper_command(tmp_path / "hooks", monkeypatch=monkeypatch)

    assert command.endswith("cyt-client-dev.sh")
    assert FISH_BREAKING_INLINE_PREFIX not in command
    assert is_unix_hook_wrapper_command(command)
    assert Path(command).is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper semantics")
def test_prefix_command_env_skips_unix_shell_wrapper() -> None:
    wrapper = "/Users/me/.cursor/hooks/cyt-client-dev.sh"
    prefixed = prefix_command_env(agent_hook_command_env(agent="cursor"), wrapper)
    assert prefixed == wrapper


@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper semantics")
def test_legacy_inline_hook_command_is_fish_incompatible() -> None:
    inline = legacy_fish_breaking_client_command()
    assert FISH_BREAKING_INLINE_PREFIX in inline
    assert_wrapper_command_is_fish_safe(inline)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper semantics")
def test_prefix_agent_hook_command_adds_fish_breaking_inline_prefix() -> None:
    from cyt.hook.cli_invocation import build_uv_run_dev_command, cyt_client_cli_script_relpath

    bare = build_uv_run_dev_command(dev_repo_root(), cyt_client_cli_script_relpath())
    prefixed = prefix_agent_hook_command(bare, agent="cursor")
    assert FISH_BREAKING_INLINE_PREFIX in prefixed
    assert_wrapper_command_is_fish_safe(prefixed)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper semantics")
def test_cursor_hook_entries_avoid_fish_breaking_inline_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr("cyt.hook.cli_invocation.cursor_hooks_dir", lambda: hooks_dir)
    repo_root = dev_repo_root()
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    entries = hook_setup.cursor_hook_entries(agent="cursor", invocation=invocation)

    for key in ("before_submit", "session_end", "pre_tool", "pre_compact"):
        command = entries[key]["command"]
        assert command.endswith(wrapper_suffix())
        assert FISH_BREAKING_INLINE_PREFIX not in command
        if fish_available():
            assert_wrapper_command_is_fish_safe(command)

    session_commands = [entry["command"] for entry in entries["session_start"]]
    assert any(
        str(command).endswith("cyt-hook-daemon-start-dev.sh") for command in session_commands
    )
    assert any(str(command).endswith(wrapper_suffix()) for command in session_commands)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix fish/bash wrapper semantics")
def test_upsert_cursor_hooks_upgrades_legacy_fish_breaking_inline_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_dir = tmp_path / "hooks"
    hooks_path = tmp_path / "hooks.json"
    monkeypatch.setattr("cyt.hook.cli_invocation.cursor_hooks_dir", lambda: hooks_dir)
    hooks_path.write_text(json.dumps(load_legacy_fish_breaking_hooks()) + "\n", encoding="utf-8")

    repo_root = dev_repo_root()
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    entries = hook_setup.cursor_hook_entries(agent="cursor", invocation=invocation)
    changed = hook_setup.upsert_cursor_hooks_into_file(
        hooks_path,
        **hook_setup.cursor_upsert_hook_kwargs(entries, config={}),
    )

    assert changed is True
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    before_submit = data["hooks"]["beforeSubmitPrompt"][0]["command"]
    assert before_submit.endswith(wrapper_suffix())
    assert FISH_BREAKING_INLINE_PREFIX not in before_submit
    wrapper_text = Path(before_submit).read_text(encoding="utf-8")
    assert "CURSOR_PROJECT_DIR" in wrapper_text
    assert str(repo_root) in wrapper_text


def test_is_hook_shell_wrapper_command_recognizes_unix_and_windows_wrappers() -> None:
    assert is_hook_shell_wrapper_command(r"C:\hooks\cyt-client-dev.cmd")
    assert is_hook_shell_wrapper_command("/Users/me/.cursor/hooks/cyt-client-dev.sh")
    assert not is_hook_shell_wrapper_command(legacy_fish_breaking_client_command())


def test_install_dev_hook_wrappers_writes_executable_bash_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrappers = install_dev_hook_wrappers(tmp_path / "hooks", monkeypatch=monkeypatch)
    client = wrappers["client"]
    text = client.read_text(encoding="utf-8")

    if sys.platform == "win32":
        assert client.name.endswith(".cmd")
        assert "EnableDelayedExpansion" in text
    else:
        assert client.name.endswith(".sh")
        assert text.startswith("#!/usr/bin/env bash")
        assert "CURSOR_PROJECT_DIR" in text
        assert client.stat().st_mode & 0o111

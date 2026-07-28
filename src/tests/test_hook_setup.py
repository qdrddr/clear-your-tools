"""Tests for `cyt hook` setup wizard."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.hook import setup_wizard as hook_setup
from cyt.hook.cli_invocation import (
    HookCliInvocation,
    build_uv_run_dev_command,
    cyt_client_cli_script_relpath,
    cyt_client_command,
    cyt_daemon_restart_command,
    cyt_daemon_start_command,
    detect_hook_cli_invocation,
    invoked_via_proxy_cli_script,
    is_dev_cyt_hook_command,
    proxy_cli_script_path,
    proxy_cli_script_relpath,
    repo_root_from_proxy_cli_script,
)


def _stub_tools_hook_wizard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hook_setup,
        "prompt_tools_hook_config",
        lambda existing, *, context: {
            "hook": {
                "tools_from": ["executor"],
                "executor_url": "http://localhost:4789",
                "mcp_definitions_file": "~/.config/cyt/mcp-definitions.json",
            },
        },
    )
    monkeypatch.setattr(hook_setup, "save_user_config", lambda *args, **kwargs: False)


def test_format_hook_stdin_test_command_uses_anonymized_payload() -> None:
    command = hook_setup.format_hook_stdin_test_command()

    assert "cat <<'EOF' | cyt-client" in command
    assert "019ebcaf" not in command
    assert "username" not in command
    assert "sess-00000000-0000-4000-8000-000000000001" in command
    assert "/Users/you/.codex/sessions/2026/06/12/rollout-example.jsonl" in command
    assert "/path/to/your/project" in command


def test_format_hook_stdin_test_command_includes_debug_flag() -> None:
    command = hook_setup.format_hook_stdin_test_command(debug=True)

    assert "CYT_HOOK_DEBUG=1 cyt-client" in command


def test_build_hook_skills_config_overlay_returns_none_when_already_configured() -> None:
    overlay = hook_setup.build_hook_skills_config_overlay(
        {
            "enabled": True,
            "directories": ["~/.claude/skills", "~/.codex/skills"],
        },
        ["~/.claude/skills", "~/.codex/skills"],
        config={"pruning": {"inject_via": "hook"}, "skills": {"enabled": True}},
    )

    assert overlay is None


def test_build_hook_skills_config_overlay_updates_inject_via_from_proxy() -> None:
    overlay = hook_setup.build_hook_skills_config_overlay(
        {
            "pipeline": "llm",
            "enabled": True,
        },
        ["~/.claude/skills"],
        config={"pruning": {"inject_via": "proxy"}},
    )

    assert overlay == {
        "pruning": {"inject_via": "hook"},
        "skills": {
            "enabled": True,
            "directories": ["~/.claude/skills"],
        },
    }


def test_cyt_hook_entry_omits_launch_agent_by_default() -> None:
    assert hook_setup.cyt_client_entry(agent="claude")["command"] == "cyt-client"
    assert hook_setup.cyt_client_entry(agent="codex")["command"] == "cyt-client"


def test_cyt_hook_entry_sets_launch_agent_when_requested() -> None:
    claude_entry = hook_setup.cyt_client_entry(agent="claude", set_launch_agent=True)
    codex_entry = hook_setup.cyt_client_entry(agent="codex", set_launch_agent=True)

    assert claude_entry["command"] == "CYT_LAUNCH_AGENT=claude cyt-client"
    assert codex_entry["command"] == "CYT_LAUNCH_AGENT=codex cyt-client"
    assert claude_entry["timeout"] == hook_setup.USER_PROMPT_TIMEOUT_SECONDS
    assert codex_entry["timeout"] == hook_setup.USER_PROMPT_TIMEOUT_SECONDS


def test_cyt_daemon_start_entry() -> None:
    assert (
        hook_setup.cyt_daemon_start_entry(agent="claude")["command"]
        == "cyt hook daemon start --unattended"
    )
    entry = hook_setup.cyt_daemon_start_entry(agent="claude", set_launch_agent=True)
    assert entry["command"] == "CYT_LAUNCH_AGENT=claude cyt hook daemon start --unattended"
    assert entry["timeout"] == hook_setup.SESSION_START_TIMEOUT_SECONDS


def test_is_cyt_hook_command_recognizes_cyt_client_and_daemon() -> None:
    client_rel = cyt_client_cli_script_relpath()
    proxy_rel = proxy_cli_script_relpath()
    assert hook_setup._is_cyt_hook_command("cyt-client")
    assert hook_setup._is_cyt_hook_command("CYT_LAUNCH_AGENT=claude cyt-client")
    assert hook_setup._is_cyt_hook_command("cyt hook daemon start")
    assert hook_setup._is_cyt_hook_command("CYT_LAUNCH_AGENT=claude cyt hook --stdin")
    assert hook_setup._is_cyt_hook_command(
        f"uv run --directory /tmp/clear-your-tools {client_rel}",
    )
    assert hook_setup._is_cyt_hook_command(
        f"uv run --directory /tmp/clear-your-tools {proxy_rel} hook daemon start --unattended",
    )
    assert not hook_setup._is_cyt_hook_command("/usr/local/bin/other-hook")


def test_detect_hook_cli_invocation_uses_dev_mode_for_proxy_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path("/tmp/clear-your-tools")
    script = repo_root / "src/cyt/proxy/cli.py"
    monkeypatch.setattr(
        "cyt.hook.cli_invocation.proxy_cli_script_path",
        lambda: script,
    )
    monkeypatch.setattr(
        "cyt.hook.cli_invocation.repo_root_from_proxy_cli_script",
        lambda: repo_root,
    )
    monkeypatch.setattr(
        "cyt.hook.cli_invocation.invoked_via_proxy_cli_script",
        lambda: True,
    )

    invocation = detect_hook_cli_invocation()

    assert invocation.is_dev is True
    assert invocation.repo_root == repo_root


def test_detect_hook_cli_invocation_uses_installed_mode_for_cyt_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt.hook.cli_invocation.invoked_via_proxy_cli_script",
        lambda: False,
    )

    invocation = detect_hook_cli_invocation()

    assert invocation.is_dev is False
    assert invocation.repo_root is None


def test_dev_hook_commands_use_uv_run_with_detected_repo_root() -> None:
    repo_root = Path("/tmp/clear-your-tools")
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    client_rel = cyt_client_cli_script_relpath()
    proxy_rel = proxy_cli_script_relpath()

    assert cyt_client_command(invocation=invocation) == build_uv_run_dev_command(
        repo_root,
        client_rel,
    )
    assert cyt_daemon_start_command(invocation=invocation) == build_uv_run_dev_command(
        repo_root,
        proxy_rel,
        "hook",
        "daemon",
        "start",
        "--unattended",
    )
    assert cyt_daemon_restart_command(invocation=invocation) == build_uv_run_dev_command(
        repo_root,
        proxy_rel,
        "hook",
        "daemon",
        "restart",
    )


def test_installed_daemon_restart_command() -> None:
    invocation = HookCliInvocation(mode="installed", repo_root=None)

    assert cyt_daemon_restart_command(invocation=invocation) == "cyt hook daemon restart"


def test_cyt_client_entry_uses_dev_command_when_invoked_via_script() -> None:
    repo_root = Path("/tmp/clear-your-tools")
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)

    entry = hook_setup.cyt_client_entry(agent="cursor", invocation=invocation)

    assert entry["command"] == build_uv_run_dev_command(
        repo_root,
        cyt_client_cli_script_relpath(),
    )


def test_cyt_daemon_start_entry_uses_dev_command_when_invoked_via_script() -> None:
    repo_root = Path("/tmp/clear-your-tools")
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)

    entry = hook_setup.cyt_daemon_start_entry(agent="cursor", invocation=invocation)

    assert entry["command"] == build_uv_run_dev_command(
        repo_root,
        proxy_cli_script_relpath(),
        "hook",
        "daemon",
        "start",
        "--unattended",
    )


def test_is_dev_cyt_hook_command() -> None:
    client_rel = cyt_client_cli_script_relpath()
    proxy_rel = proxy_cli_script_relpath()
    assert is_dev_cyt_hook_command(
        f"uv run --directory /tmp/repo {client_rel}",
    )
    assert is_dev_cyt_hook_command(
        f"uv run --directory /tmp/repo {proxy_rel} hook daemon start --unattended",
    )
    assert is_dev_cyt_hook_command(
        f"uv run --directory /tmp/repo {proxy_rel} hook daemon restart",
    )
    assert not is_dev_cyt_hook_command("cyt-client")
    assert not is_dev_cyt_hook_command("uv run --directory /tmp/repo other.py")


def test_repo_root_from_proxy_cli_script_resolves_from_package_layout() -> None:
    script = proxy_cli_script_path()
    repo_root = repo_root_from_proxy_cli_script()

    assert script.name == "cli.py"
    assert repo_root is not None
    assert (repo_root / "pyproject.toml").is_file()
    assert script == repo_root / proxy_cli_script_relpath()


def test_invoked_via_proxy_cli_script_matches_script_argv0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = proxy_cli_script_path()
    monkeypatch.setattr(sys, "argv", [str(script), "hook", "cursor"])

    assert invoked_via_proxy_cli_script() is True


def test_invoked_via_proxy_cli_script_matches_cli_impl_after_runpy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = proxy_cli_script_path().with_name("cli_impl.py")
    monkeypatch.setattr(sys, "argv", [str(script), "hook", "cursor"])

    assert invoked_via_proxy_cli_script() is True


def test_detect_hook_cli_invocation_after_runpy_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = repo_root_from_proxy_cli_script()
    assert repo_root is not None
    script = proxy_cli_script_path().with_name("cli_impl.py")
    monkeypatch.setattr(sys, "argv", [str(script), "hook", "cursor"])

    invocation = detect_hook_cli_invocation()

    assert invocation.is_dev is True
    assert invocation.repo_root == repo_root


def test_run_hook_setup_installs_dev_cursor_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = Path("/tmp/clear-your-tools")
    cursor_path = tmp_path / "cursor" / "hooks.json"
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(
        hook_setup,
        "_configure_hook_skills_directories",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(hook_setup, "detect_hook_cli_invocation", lambda: invocation)

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        lowered = text.lower()
        if "cyt_launch_agent" in lowered:
            return False
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    _stub_tools_hook_wizard(monkeypatch)

    with (
        patch("cyt.hook.setup_wizard.load_config", return_value={"skills": {"enabled": True}}),
        patch("cyt.hook.daemon.daemon_restart") as daemon_restart,
    ):
        hook_setup.run_hook_setup(agents=["cursor"])

    daemon_restart.assert_called_once_with(config_path=None, unattended=False)

    data = json.loads(cursor_path.read_text(encoding="utf-8"))
    client_rel = cyt_client_cli_script_relpath()
    proxy_rel = proxy_cli_script_relpath()
    assert data["hooks"]["beforeSubmitPrompt"][0]["command"] == build_uv_run_dev_command(
        repo_root,
        client_rel,
    )
    assert data["hooks"]["sessionStart"][0]["command"] == build_uv_run_dev_command(
        repo_root,
        proxy_rel,
        "hook",
        "daemon",
        "start",
        "--unattended",
    )
    assert data["hooks"]["sessionEnd"][0]["command"] == build_uv_run_dev_command(
        repo_root,
        client_rel,
    )

    output = capsys.readouterr().out
    restart_command = cyt_daemon_restart_command(invocation=invocation)
    assert "Restarting hook daemon via development CLI:" in output
    assert f"  {restart_command}" in output


def test_merge_cyt_hook_upgrades_env_prefixed_legacy_stdin_command() -> None:
    existing = {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "CYT_LAUNCH_AGENT=claude cyt hook --stdin --debug",
                        "timeout": 60,
                    },
                ],
            },
        ],
    }
    entry = hook_setup.cyt_client_entry()
    merged, changed = hook_setup.merge_cyt_hook(existing, entry)

    assert changed is True
    commands = [hook["command"] for hook in merged["UserPromptSubmit"][0]["hooks"]]
    assert commands == ["cyt-client"]


def test_upsert_cyt_hook_replaces_duplicate_debug_variants() -> None:
    existing = {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "CYT_LAUNCH_AGENT=claude cyt hook --stdin --debug",
                        "timeout": 60,
                    },
                    {
                        "type": "command",
                        "command": "CYT_LAUNCH_AGENT=claude cyt hook --stdin",
                        "timeout": 60,
                    },
                    {
                        "type": "command",
                        "command": "/usr/local/bin/other-hook",
                        "timeout": 10,
                    },
                ],
            },
        ],
    }
    entry = hook_setup.cyt_client_entry()
    merged, changed = hook_setup.upsert_cyt_hook(existing, entry)

    assert changed is True
    hooks = merged["UserPromptSubmit"][0]["hooks"]
    assert len(hooks) == 2
    commands = [hook["command"] for hook in hooks]
    assert "cyt-client" in commands
    assert "/usr/local/bin/other-hook" in commands


def test_upsert_cyt_hook_is_noop_when_settings_match() -> None:
    existing = {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "cyt-client",
                        "timeout": 30,
                    },
                ],
            },
        ],
    }
    entry = hook_setup.cyt_client_entry()
    merged, changed = hook_setup.upsert_cyt_hook(existing, entry)

    assert changed is False
    assert merged == existing


def test_merge_cyt_hook_adds_user_prompt_submit_entry() -> None:
    entry = hook_setup.cyt_client_entry()
    merged, changed = hook_setup.merge_cyt_hook({}, entry)

    assert changed is True
    assert merged["UserPromptSubmit"][0]["hooks"][0]["command"] == "cyt-client"


def test_merge_cyt_hook_upgrades_legacy_stdin_command() -> None:
    existing = {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {"type": "command", "command": "cyt hook --stdin --debug", "timeout": 60},
                ],
            },
        ],
    }
    entry = hook_setup.cyt_client_entry()
    merged, changed = hook_setup.merge_cyt_hook(existing, entry)

    assert changed is True
    assert merged["UserPromptSubmit"][0]["hooks"][0]["command"] == "cyt-client"


def test_merge_cyt_hook_skips_legacy_cyt_skills_command() -> None:
    existing = {
        "UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "cyt skills", "timeout": 60}]},
        ],
    }
    entry = hook_setup.cyt_client_entry()
    merged, changed = hook_setup.merge_cyt_hook(existing, entry)

    assert changed is False
    assert merged == existing


def test_merge_hooks_into_file_writes_claude_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"env":{"FOO":"bar"}}\n', encoding="utf-8")
    entry = hook_setup.cyt_client_entry()

    changed = hook_setup.merge_hooks_into_file(path, entry)

    assert changed is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["env"]["FOO"] == "bar"
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "cyt-client"


def test_merge_hooks_into_file_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    entry = hook_setup.cyt_client_entry()

    assert hook_setup.merge_hooks_into_file(path, entry) is True
    assert hook_setup.merge_hooks_into_file(path, entry) is False


def test_merge_skills_directory_lists_appends_without_duplicates() -> None:
    merged, changed = hook_setup.merge_skills_directory_lists(
        ["~/.claude/skills"],
        ["~/.claude/skills", "~/.codex/skills"],
    )

    assert changed is True
    assert merged == ["~/.claude/skills", "~/.codex/skills"]


def test_agent_config_ready_expands_tilde_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / "settings.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    assert hook_setup._agent_config_ready(Path("~/.claude/settings.json")) is True
    assert hook_setup._agent_config_ready(Path("~/.missing/settings.json")) is False


def test_default_hook_skills_directories_uses_existing_config() -> None:
    dirs = hook_setup.default_hook_skills_directories(
        {"directories": ["/custom/skills"]},
        include_claude=True,
        include_codex=True,
    )

    assert dirs == ["/custom/skills"]


def test_default_hook_skills_directories_uses_agent_defaults() -> None:
    claude_only = hook_setup.default_hook_skills_directories(
        {},
        include_claude=True,
        include_codex=False,
    )
    both = hook_setup.default_hook_skills_directories(
        {},
        include_claude=True,
        include_codex=True,
    )
    cursor_only = hook_setup.default_hook_skills_directories(
        {"directories": ["~/.claude/skills"]},
        include_claude=False,
        include_codex=False,
        include_cursor=True,
    )

    assert claude_only == ["~/.claude/skills"]
    assert both == ["~/.claude/skills", "~/.codex/skills"]
    assert cursor_only == ["~/.cursor/skills"]


def test_save_hook_skills_directories_writes_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"

    changed = hook_setup._save_hook_skills_directories(
        config_path,
        ["~/.claude/skills", "~/.codex/skills"],
        user_overlay={},
    )

    assert changed is True
    text = config_path.read_text(encoding="utf-8")
    assert "~/.claude/skills" in text
    assert "~/.codex/skills" in text
    assert "enabled: true" in text
    assert "pruning:" in text
    assert "inject_via: hook" in text


def test_save_hook_skills_directories_preserves_pipeline_and_updates_inject_via(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "skills:\n  pipeline: llm\n  enabled: true\n\npruning:\n  inject_via: proxy\n",
        encoding="utf-8",
    )
    user_overlay = {
        "skills": {"pipeline": "llm", "enabled": True},
        "pruning": {"inject_via": "proxy"},
    }

    changed = hook_setup._save_hook_skills_directories(
        config_path,
        ["~/.claude/skills"],
        user_overlay=user_overlay,
    )

    assert changed is True
    text = config_path.read_text(encoding="utf-8")
    assert "pipeline: llm" in text
    assert "inject_via: hook" in text
    assert "inject_via: proxy" not in text
    assert "enabled: true" in text
    assert "~/.claude/skills" in text


def test_save_hook_skills_directories_skips_when_already_configured(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "skills:\n"
        "  pipeline: llm\n"
        "  enabled: true\n"
        "  directories:\n"
        "    - ~/.claude/skills\n"
        "    - ~/.codex/skills\n"
        "pruning:\n"
        "  inject_via: hook\n",
        encoding="utf-8",
    )
    user_overlay = {
        "skills": {
            "pipeline": "llm",
            "enabled": True,
            "directories": ["~/.claude/skills", "~/.codex/skills"],
        },
        "pruning": {"inject_via": "hook"},
    }

    changed = hook_setup._save_hook_skills_directories(
        config_path,
        ["~/.claude/skills", "~/.codex/skills"],
        user_overlay=user_overlay,
    )

    assert changed is False


def test_run_hook_setup_updates_duplicate_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    claude_path = claude_dir / "settings.json"
    claude_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "CYT_LAUNCH_AGENT=claude cyt hook --stdin --debug",
                                    "timeout": hook_setup.HOOK_TIMEOUT_SECONDS,
                                },
                                {
                                    "type": "command",
                                    "command": "CYT_LAUNCH_AGENT=claude cyt hook --stdin",
                                    "timeout": hook_setup.HOOK_TIMEOUT_SECONDS,
                                },
                            ],
                        },
                    ],
                },
            },
        )
        + "\n",
        encoding="utf-8",
    )
    missing_codex = tmp_path / "missing-codex" / "hooks.json"

    monkeypatch.setattr(hook_setup, "CLAUDE_SETTINGS_PATH", claude_path)
    monkeypatch.setattr(hook_setup, "CODEX_HOOKS_PATH", missing_codex)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(
        hook_setup,
        "_configure_hook_skills_directories",
        lambda **_kwargs: None,
    )

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        lowered = text.lower()
        if "debug" in lowered or "cyt_launch_agent" in lowered:
            return False
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    monkeypatch.setattr(hook_setup, "_prompt_choice", lambda *_args, **_kwargs: "update")
    _stub_tools_hook_wizard(monkeypatch)

    with patch("cyt.hook.setup_wizard.load_config", return_value={"skills": {"enabled": True}}):
        hook_setup.run_hook_setup(agents=["claude", "codex"])

    claude_data = json.loads(claude_path.read_text(encoding="utf-8"))
    hooks = claude_data["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert len(hooks) == 1
    assert hooks[0]["command"] == "cyt-client"
    assert claude_data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == (
        "cyt hook daemon start --unattended"
    )


def test_run_hook_setup_merges_existing_agent_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_dir = tmp_path / "claude"
    codex_dir = tmp_path / "codex"
    claude_dir.mkdir()
    codex_dir.mkdir()
    claude_path = claude_dir / "settings.json"
    codex_path = codex_dir / "hooks.json"
    claude_path.write_text("{}\n", encoding="utf-8")
    codex_path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(hook_setup, "CLAUDE_SETTINGS_PATH", claude_path)
    monkeypatch.setattr(hook_setup, "CODEX_HOOKS_PATH", codex_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(
        hook_setup,
        "_configure_hook_skills_directories",
        lambda **_kwargs: None,
    )

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        lowered = text.lower()
        if "debug" in lowered or "cyt_launch_agent" in lowered:
            return False
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    _stub_tools_hook_wizard(monkeypatch)

    with patch("cyt.hook.setup_wizard.load_config", return_value={"skills": {"enabled": True}}):
        hook_setup.run_hook_setup(agents=["claude", "codex"])

    claude_data = json.loads(claude_path.read_text(encoding="utf-8"))
    codex_data = json.loads(codex_path.read_text(encoding="utf-8"))
    assert claude_data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "cyt-client"
    assert codex_data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "cyt-client"
    assert claude_data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == (
        "cyt hook daemon start --unattended"
    )
    assert codex_data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == (
        "cyt hook daemon start --unattended"
    )


def test_run_hook_setup_skips_declined_agent_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude_dir = tmp_path / "claude"
    codex_dir = tmp_path / "codex"
    claude_dir.mkdir()
    codex_dir.mkdir()
    claude_path = claude_dir / "settings.json"
    codex_path = codex_dir / "hooks.json"
    claude_path.write_text("{}\n", encoding="utf-8")
    codex_path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(hook_setup, "CLAUDE_SETTINGS_PATH", claude_path)
    monkeypatch.setattr(hook_setup, "CODEX_HOOKS_PATH", codex_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(
        hook_setup,
        "_configure_hook_skills_directories",
        lambda **_kwargs: None,
    )

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        if "debug" in text.lower():
            return False
        return "Claude" in text

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    _stub_tools_hook_wizard(monkeypatch)

    with patch("cyt.hook.setup_wizard.load_config", return_value={"skills": {"enabled": True}}):
        hook_setup.run_hook_setup(agents=["claude", "codex"])

    claude_data = json.loads(claude_path.read_text(encoding="utf-8"))
    codex_data = json.loads(codex_path.read_text(encoding="utf-8"))
    assert "hooks" in claude_data
    assert "hooks" not in codex_data
    captured = capsys.readouterr()
    assert "Codex: skipped" in captured.out


def test_run_hook_setup_skips_missing_agent_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_claude = tmp_path / "missing-claude" / "settings.json"
    missing_codex = tmp_path / "missing-codex" / "hooks.json"
    monkeypatch.setattr(hook_setup, "CLAUDE_SETTINGS_PATH", missing_claude)
    monkeypatch.setattr(hook_setup, "CODEX_HOOKS_PATH", missing_codex)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    _stub_tools_hook_wizard(monkeypatch)

    with pytest.raises(SystemExit, match="No agent config files found"):
        with patch("cyt.hook.setup_wizard.load_config", return_value={"skills": {"enabled": True}}):
            hook_setup.run_hook_setup(agents=["claude", "codex"])

    captured = capsys.readouterr()
    assert "Skipping Claude" in captured.out
    assert "config file not found" in captured.out
    assert "Skipping Codex" in captured.out


def test_remove_cyt_hooks_removes_only_cyt_commands() -> None:
    existing = {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {"type": "command", "command": "cyt hook --stdin", "timeout": 60},
                    {"type": "command", "command": "/usr/local/bin/other-hook", "timeout": 10},
                ],
            },
        ],
    }

    merged, changed = hook_setup.remove_cyt_hooks(existing)

    assert changed is True
    assert merged["UserPromptSubmit"][0]["hooks"] == [
        {"type": "command", "command": "/usr/local/bin/other-hook", "timeout": 10},
    ]


def test_uninstall_hooks_from_file_preserves_other_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "env": {"FOO": "bar"},
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {"type": "command", "command": "cyt skills", "timeout": 60},
                            ],
                        },
                    ],
                },
            },
        )
        + "\n",
        encoding="utf-8",
    )

    changed = hook_setup.uninstall_hooks_from_file(path)

    assert changed is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["env"]["FOO"] == "bar"
    assert "hooks" not in data


def test_run_hook_uninstall_removes_hooks_from_agent_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_path = tmp_path / "claude" / "settings.json"
    codex_path = tmp_path / "codex" / "hooks.json"
    claude_path.parent.mkdir(parents=True)
    codex_path.parent.mkdir(parents=True)
    entry = hook_setup.cyt_client_entry()
    hook_setup.merge_hooks_into_file(claude_path, entry)
    hook_setup.merge_hooks_into_file(codex_path, entry)

    monkeypatch.setattr(hook_setup, "CLAUDE_SETTINGS_PATH", claude_path)
    monkeypatch.setattr(hook_setup, "CODEX_HOOKS_PATH", codex_path)

    hook_setup.run_hook_uninstall()

    assert "hooks" not in json.loads(claude_path.read_text(encoding="utf-8"))
    assert "hooks" not in json.loads(codex_path.read_text(encoding="utf-8"))


def test_hook_uninstall_cli_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["cyt", "hook", "--uninstall"])
    called = {"run": False}

    def fake_run_hook_uninstall() -> None:
        called["run"] = True

    monkeypatch.setattr("cyt.hook.setup_wizard.run_hook_uninstall", fake_run_hook_uninstall)

    from cyt.proxy.cli_impl import main

    main()
    assert called["run"] is True


def test_hook_wizard_without_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["cyt", "hook", "all"])
    called: dict[str, bool | list[str] | None] = {"run": False, "agents": None}

    def fake_run_hook_setup(
        *,
        config_path: Path | None = None,
        agents: list[str] | None = None,
    ) -> None:
        called["run"] = True
        called["agents"] = agents
        assert config_path is None

    monkeypatch.setattr("cyt.hook.setup_wizard.run_hook_setup", fake_run_hook_setup)

    from cyt.proxy.cli_impl import main

    main()
    assert called["run"] is True
    assert called["agents"] is None


def test_hook_cursor_cli_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["cyt", "hook", "cursor"])
    called: dict[str, list[str] | None] = {"agents": None}

    def fake_run_hook_setup(
        *,
        config_path: Path | None = None,
        agents: list[str] | None = None,
    ) -> None:
        called["agents"] = agents
        assert config_path is None

    monkeypatch.setattr("cyt.hook.setup_wizard.run_hook_setup", fake_run_hook_setup)

    from cyt.proxy.cli_impl import main

    main()
    assert called["agents"] == ["cursor"]


def test_bare_hook_requires_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["cyt", "hook"])

    from cyt.proxy.cli_impl import main

    with pytest.raises(SystemExit, match="usage: cyt hook"):
        main()


def test_upsert_cursor_hooks_into_file_writes_flat_entries(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    entries = hook_setup.cursor_hook_entries(agent="cursor")

    changed = hook_setup.upsert_cursor_hooks_into_file(
        path,
        before_submit_entry=entries["before_submit"],
        session_start_entry=entries["session_start"],
        session_end_entry=entries["session_end"],
    )

    assert changed is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert isinstance(data["hooks"], dict)
    assert data["hooks"]["beforeSubmitPrompt"] == [entries["before_submit"]]
    assert data["hooks"]["sessionStart"] == [entries["session_start"]]
    assert data["hooks"]["sessionEnd"] == [entries["session_end"]]
    assert entries["before_submit"]["command"] == "cyt-client"
    assert entries["session_start"]["command"] == "cyt hook daemon start --unattended"


def test_normalize_cursor_hooks_section_drops_claude_nested_shape() -> None:
    normalized = hook_setup.normalize_cursor_hooks_section(
        {
            "beforeSubmitPrompt": [
                {
                    "hooks": [
                        {"type": "command", "command": "CYT_LAUNCH_AGENT=claude cyt-client"},
                    ],
                },
            ],
        },
    )

    assert normalized == {}


def test_upsert_cursor_hooks_into_file_repairs_non_object_hooks(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"version": 1, "hooks": []}) + "\n", encoding="utf-8")

    changed = hook_setup.upsert_cursor_hooks_into_file(
        path,
        before_submit_entry=hook_setup.cursor_before_submit_entry(agent="cursor"),
        session_start_entry=hook_setup.cursor_session_start_entry(agent="cursor"),
        session_end_entry=hook_setup.cursor_session_end_entry(agent="cursor"),
    )

    assert changed is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data["hooks"], dict)
    assert "beforeSubmitPrompt" in data["hooks"]


def test_run_hook_setup_installs_cursor_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cursor_path = tmp_path / "cursor" / "hooks.json"
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(
        hook_setup,
        "_configure_hook_skills_directories",
        lambda **_kwargs: None,
    )

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        lowered = text.lower()
        if "cyt_launch_agent" in lowered:
            return False
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    _stub_tools_hook_wizard(monkeypatch)

    with (
        patch("cyt.hook.setup_wizard.load_config", return_value={"skills": {"enabled": True}}),
        patch("cyt.hook.daemon.daemon_restart") as daemon_restart,
    ):
        hook_setup.run_hook_setup(agents=["cursor"])

    daemon_restart.assert_called_once_with(config_path=None, unattended=False)

    data = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert data["hooks"]["beforeSubmitPrompt"][0]["command"] == "cyt-client"
    assert data["hooks"]["sessionStart"][0]["command"] == "cyt hook daemon start --unattended"
    assert data["hooks"]["sessionEnd"][0]["command"] == "cyt-client"

    output = capsys.readouterr().out
    assert "Restarting hook daemon via packaged cyt:" in output
    assert f"  {cyt_daemon_restart_command()}" in output


def test_run_hook_setup_skips_cursor_daemon_restart_when_declined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cursor_path = tmp_path / "cursor" / "hooks.json"
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(
        hook_setup,
        "_configure_hook_skills_directories",
        lambda **_kwargs: None,
    )

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        lowered = text.lower()
        if "cyt_launch_agent" in lowered:
            return False
        if "restart the hook daemon" in lowered:
            return False
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    _stub_tools_hook_wizard(monkeypatch)

    with (
        patch("cyt.hook.setup_wizard.load_config", return_value={"skills": {"enabled": True}}),
        patch("cyt.hook.daemon.daemon_restart") as daemon_restart,
    ):
        hook_setup.run_hook_setup(agents=["cursor"])

    daemon_restart.assert_not_called()
    output = capsys.readouterr().out
    assert "Skipped. Run manually when ready:" in output
    assert f"  {cyt_daemon_restart_command()}" in output

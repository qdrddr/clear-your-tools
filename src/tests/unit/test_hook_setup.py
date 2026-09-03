"""Tests for `cyt hook` setup wizard."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
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


@pytest.fixture(autouse=True)
def _bare_hook_executables_for_setup_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep legacy bare command expectations stable when cyt/uv are on PATH."""
    monkeypatch.setattr(
        "cyt_client.hook_executable.resolve_hook_executable",
        lambda name: name,
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


def _prevent_hallucinations_blocked_prompt(text: str) -> None:
    lowered = text.lower()
    for fragment in (
        "skills directories",
        "cyt_launch_agent",
        "hook debug logging",
        "start the hook daemon",
    ):
        if fragment in lowered:
            raise AssertionError(f"{fragment} prompt should be skipped")


def _stub_prevent_hallucinations_prompts(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prompt_calls: list[str] | None = None,
) -> list[str]:
    calls = prompt_calls if prompt_calls is not None else []

    def fake_prompt_choice(text: str, *args: object, **kwargs: object) -> str:
        calls.append(text)
        _prevent_hallucinations_blocked_prompt(text)
        if "choose action" in text:
            return "update"
        raise AssertionError(f"unexpected prompt: {text!r}")

    def fail_unexpected_yes_no(text: str, *args: object, **kwargs: object) -> bool:
        _prevent_hallucinations_blocked_prompt(text)
        return True

    monkeypatch.setattr(hook_setup, "_prompt", lambda text, default="": default)
    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fail_unexpected_yes_no)
    monkeypatch.setattr(hook_setup, "_prompt_choice", fake_prompt_choice)
    return calls


def _write_duplicate_cursor_hooks(cursor_path: Path) -> None:
    entries = hook_setup.cursor_hook_entries(agent="cursor", include_post_tool_use=True)
    cursor_path.parent.mkdir(parents=True)
    cursor_path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "beforeSubmitPrompt": [entries["before_submit"], entries["before_submit"]],
                    "sessionStart": entries["session_start"],
                    "sessionEnd": [entries["session_end"]],
                    "preToolUse": [entries["pre_tool"]],
                    "postToolUse": [entries["post_tool"]],
                    "preCompact": [entries["pre_compact"]],
                },
            },
        )
        + "\n",
        encoding="utf-8",
    )


def test_format_hook_stdin_test_command_uses_anonymized_payload() -> None:
    command = hook_setup.format_hook_stdin_test_command()

    if sys.platform == "win32":
        assert command.startswith("@'")
        assert "'@ | " in command
    else:
        assert "cat <<'EOF' | cyt-client" in command
    assert "019ebcaf" not in command
    assert "username" not in command
    assert "sess-00000000-0000-4000-8000-000000000001" in command
    assert "beforeSubmitPrompt" in command
    assert "workspace_roots" in command


def test_format_hook_stdin_test_command_verify_only_uses_pre_tool_use_payload() -> None:
    command = hook_setup.format_hook_stdin_test_command(
        verify_only=True,
        selected_agents=["claude"],
    )

    assert '"hook_event_name": "preToolUse"' in command
    assert '"tool_name": "Shell"' in command
    assert "UserPromptSubmit" not in command


def test_format_hook_stdin_test_command_verify_only_cursor_uses_user_prompt_submit() -> None:
    command = hook_setup.format_hook_stdin_test_command(
        verify_only=True,
        selected_agents=["cursor"],
    )

    assert '"hook_event_name": "beforeSubmitPrompt"' in command
    assert "preToolUse" not in command


def test_format_hook_stdin_test_command_includes_debug_flag() -> None:
    command = hook_setup.format_hook_stdin_test_command(debug=True)

    if sys.platform == "win32":
        assert "CYT_HOOK_DEBUG=1" in command
    else:
        assert "CYT_HOOK_DEBUG=1 cyt-client" in command


def test_build_hook_skills_config_overlay_returns_none_when_already_configured() -> None:
    overlay = hook_setup.build_hook_skills_config_overlay(
        {
            "enabled": True,
            "directories": ["~/.claude/skills", "~/.codex/skills"],
        },
        ["~/.claude/skills", "~/.codex/skills"],
        config={
            "pruning": {"inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"}},
            "skills": {"enabled": True},
        },
    )

    assert overlay is None


def test_build_hook_skills_config_overlay_updates_inject_via_from_proxy() -> None:
    overlay = hook_setup.build_hook_skills_config_overlay(
        {
            "pipeline": "llm",
            "enabled": True,
        },
        ["~/.claude/skills"],
        enabled=True,
        config={"pruning": {"inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"}}},
    )

    assert overlay == {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
        },
        "skills": {
            "enabled": True,
            "directories": ["~/.claude/skills"],
        },
    }


def test_build_hook_skills_config_overlay_disables_skills() -> None:
    overlay = hook_setup.build_hook_skills_config_overlay(
        {"enabled": True, "directories": ["~/.cursor/skills"]},
        enabled=False,
        config={"pruning": {"inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"}}},
    )

    assert overlay == {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
        },
        "skills": {"enabled": False},
    }


def test_cyt_hook_entry_omits_launch_agent_by_default() -> None:
    assert hook_setup.cyt_client_entry(agent="claude")["command"] == "cyt-client"
    assert hook_setup.cyt_client_entry(agent="codex")["command"] == "cyt-client"


def test_cyt_hook_entry_sets_launch_agent_when_requested() -> None:
    claude_entry = hook_setup.cyt_client_entry(agent="claude", set_launch_agent=True)
    codex_entry = hook_setup.cyt_client_entry(agent="codex", set_launch_agent=True)

    if sys.platform == "win32":
        assert "CYT_LAUNCH_AGENT=claude" in claude_entry["command"]
        assert "cyt-client" in claude_entry["command"]
        assert "CYT_LAUNCH_AGENT=codex" in codex_entry["command"]
    else:
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
    if sys.platform == "win32":
        assert "CYT_LAUNCH_AGENT=claude" in entry["command"]
        assert "cyt hook daemon start --unattended" in entry["command"]
    else:
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


def test_dev_hook_commands_windows_repo_root_round_trip_recognition() -> None:
    """Dev commands with ``C:\\`` repo paths survive build + cyt-hook recognition."""
    repo_root = Path("C:/Users/me/git/clear-your-tools")
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    client_cmd = cyt_client_command(invocation=invocation)
    daemon_cmd = cyt_daemon_start_command(invocation=invocation)

    assert (
        r"C:\Users\me\git\clear-your-tools" in client_cmd
        or "C:/Users/me/git/clear-your-tools" in client_cmd
    )
    assert hook_setup._is_cyt_hook_command(client_cmd)
    assert is_dev_cyt_hook_command(client_cmd)
    assert hook_setup._is_cyt_hook_command(daemon_cmd)
    assert is_dev_cyt_hook_command(daemon_cmd)


def test_build_hook_spawn_command_dev_mode_uses_uv_and_repo_cli() -> None:
    from cyt.hook.cli_invocation import build_hook_spawn_command

    repo_root = Path(r"C:\Users\me\git\clear-your-tools")
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    argv = build_hook_spawn_command(port=8834, config_path=None, invocation=invocation)

    assert Path(argv[0]).name.lower().startswith(("uv", "python"))
    assert argv[argv.index("--directory") + 1] == str(repo_root)
    assert "src/cyt/proxy/cli.py" in argv
    assert "proxy" in argv
    assert "8834" in argv


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
        f'"C:\\tools\\uv.exe" run --directory /tmp/repo {client_rel}',
    )
    assert is_dev_cyt_hook_command(
        f"uv run --directory /tmp/repo {proxy_rel} hook daemon start --unattended",
    )
    assert is_dev_cyt_hook_command(
        f"uv run --directory /tmp/repo {proxy_rel} hook daemon restart",
    )
    assert not is_dev_cyt_hook_command("cyt-client")
    assert not is_dev_cyt_hook_command("uv run --directory /tmp/repo other.py")


def test_is_dev_cyt_hook_command_without_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cyt.hook.cli_invocation.repo_root_from_proxy_cli_script",
        lambda: None,
    )
    assert is_dev_cyt_hook_command(
        "uv run --directory /tmp/repo src/cyt_client/cli.py",
    )
    assert is_dev_cyt_hook_command(
        "uv run --directory /tmp/repo src/cyt/proxy/cli.py hook daemon start --unattended",
    )
    assert not is_dev_cyt_hook_command("cyt-client")


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
    hooks_dir = tmp_path / "cursor" / "hooks"
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_path)
    monkeypatch.setattr(
        "cyt.hook.cli_invocation.cursor_hooks_dir",
        lambda: hooks_dir,
    )
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(
        hook_setup,
        "_configure_hook_skills",
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
        patch("cyt.hook.daemon.daemon_start") as daemon_start,
    ):
        hook_setup.run_hook_setup(agents=["cursor"])

    daemon_start.assert_called_once_with(config_path=None, unattended=True)

    data = json.loads(cursor_path.read_text(encoding="utf-8"))
    client_rel = cyt_client_cli_script_relpath()
    proxy_rel = proxy_cli_script_relpath()
    client_cmd = data["hooks"]["beforeSubmitPrompt"][0]["command"]
    daemon_cmd = data["hooks"]["sessionStart"][0]["command"]
    if sys.platform == "win32":
        assert client_cmd.endswith("cyt-client-dev.cmd")
        assert daemon_cmd.endswith("cyt-hook-daemon-start-dev.cmd")
        assert data["hooks"]["sessionStart"][1]["command"].endswith("cyt-client-dev.cmd")
        assert data["hooks"]["sessionEnd"][0]["command"].endswith("cyt-client-dev.cmd")
        client_wrapper = Path(client_cmd)
        assert client_wrapper.is_file()
        wrapper_text = client_wrapper.read_text(encoding="utf-8")
        assert " run --directory " in wrapper_text
        assert str(repo_root) in wrapper_text or "/tmp/clear-your-tools" in wrapper_text
    else:
        assert client_cmd == build_uv_run_dev_command(repo_root, client_rel)
        assert daemon_cmd == build_uv_run_dev_command(
            repo_root,
            proxy_rel,
            "hook",
            "daemon",
            "start",
            "--unattended",
        )
        assert data["hooks"]["sessionStart"][1]["command"] == build_uv_run_dev_command(
            repo_root,
            client_rel,
        )
        assert data["hooks"]["sessionEnd"][0]["command"] == build_uv_run_dev_command(
            repo_root,
            client_rel,
        )

    output = capsys.readouterr().out
    start_command = cyt_daemon_start_command(invocation=invocation)
    assert "\nStarting hook daemon via development CLI:" in output
    assert f"  {start_command}" in output


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
    assert "cursor: hook" in text
    assert "claude: hook" in text


def test_save_hook_skills_directories_preserves_pipeline_and_updates_inject_via(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "skills:\n"
        "  pipeline: llm\n"
        "  enabled: true\n\n"
        "pruning:\n"
        "  inject_via:\n"
        "    cursor: hook\n"
        "    claude: proxy\n"
        "    codex: proxy\n",
        encoding="utf-8",
    )
    user_overlay = {
        "skills": {"pipeline": "llm", "enabled": True},
        "pruning": {"inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"}},
    }

    changed = hook_setup._save_hook_skills_directories(
        config_path,
        ["~/.claude/skills"],
        user_overlay=user_overlay,
    )

    assert changed is True
    text = config_path.read_text(encoding="utf-8")
    assert "pipeline: llm" in text
    assert "cursor: hook" in text
    assert "claude: hook" in text
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
        "  inject_via:\n"
        "    cursor: hook\n"
        "    claude: hook\n"
        "    codex: hook\n",
        encoding="utf-8",
    )
    user_overlay = {
        "skills": {
            "pipeline": "llm",
            "enabled": True,
            "directories": ["~/.claude/skills", "~/.codex/skills"],
        },
        "pruning": {"inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"}},
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
        "_configure_hook_skills",
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
        "_configure_hook_skills",
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
        "_configure_hook_skills",
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


def test_uninstall_cursor_hooks_preserves_minimum_structure(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    entries = hook_setup.cursor_hook_entries(agent="cursor")
    hook_setup.upsert_cursor_hooks_into_file(
        path,
        before_submit_entry=entries["before_submit"],
        session_start_entries=entries["session_start"],
        session_end_entry=entries["session_end"],
        pre_tool_entry=entries["pre_tool"],
        post_tool_entry=entries["post_tool"],
    )

    changed = hook_setup.uninstall_hooks_from_file(path, preserve_empty_hooks_object=True)

    assert changed is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"version": 1, "hooks": {}}


def test_uninstall_cursor_hooks_preserves_non_cyt_hooks(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    entries = hook_setup.cursor_hook_entries(agent="cursor")
    hook_setup.upsert_cursor_hooks_into_file(
        path,
        before_submit_entry=entries["before_submit"],
        session_start_entries=entries["session_start"],
        session_end_entry=entries["session_end"],
        pre_tool_entry=entries["pre_tool"],
        post_tool_entry=entries["post_tool"],
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["hooks"]["beforeShellExecution"] = [{"command": ".cursor/hooks/approve-network.sh"}]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    changed = hook_setup.uninstall_hooks_from_file(path, preserve_empty_hooks_object=True)

    assert changed is True
    remaining = json.loads(path.read_text(encoding="utf-8"))
    assert remaining["version"] == 1
    assert remaining["hooks"] == {
        "beforeShellExecution": [{"command": ".cursor/hooks/approve-network.sh"}],
    }


def test_run_hook_uninstall_preserves_cursor_minimum_structure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor_path = tmp_path / "cursor" / "hooks.json"
    cursor_path.parent.mkdir(parents=True)
    entries = hook_setup.cursor_hook_entries(agent="cursor")
    hook_setup.upsert_cursor_hooks_into_file(
        cursor_path,
        before_submit_entry=entries["before_submit"],
        session_start_entries=entries["session_start"],
        session_end_entry=entries["session_end"],
        pre_tool_entry=entries["pre_tool"],
        post_tool_entry=entries["post_tool"],
    )
    missing_claude = tmp_path / "missing-claude" / "settings.json"
    missing_codex = tmp_path / "missing-codex" / "hooks.json"
    monkeypatch.setattr(hook_setup, "CLAUDE_SETTINGS_PATH", missing_claude)
    monkeypatch.setattr(hook_setup, "CODEX_HOOKS_PATH", missing_codex)
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_path)

    hook_setup.run_hook_uninstall()

    data = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert data == {"version": 1, "hooks": {}}


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
    called: dict[str, object] = {"run": False, "agents": "unset"}

    def fake_run_hook_uninstall(*, agents: list[str] | None = None) -> None:
        called["run"] = True
        called["agents"] = agents

    monkeypatch.setattr("cyt.hook.setup_wizard.run_hook_uninstall", fake_run_hook_uninstall)

    from cyt.proxy.cli_impl import main

    main()
    assert called["run"] is True
    assert called["agents"] is None


@pytest.mark.parametrize(
    ("argv", "expected_agents"),
    [
        (["cyt", "hook", "all", "--uninstall"], None),
        (["cyt", "hook", "cursor", "--uninstall"], ["cursor"]),
        (["cyt", "hook", "claude", "--uninstall"], ["claude"]),
        (["cyt", "hook", "codex", "--uninstall"], ["codex"]),
    ],
)
def test_hook_uninstall_cli_routing_for_agents(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected_agents: list[str] | None,
) -> None:
    monkeypatch.setattr("sys.argv", argv)
    called: dict[str, object] = {"run": False, "agents": "unset"}

    def fake_run_hook_uninstall(*, agents: list[str] | None = None) -> None:
        called["run"] = True
        called["agents"] = agents

    monkeypatch.setattr("cyt.hook.setup_wizard.run_hook_uninstall", fake_run_hook_uninstall)

    from cyt.proxy.cli_impl import main

    main()
    assert called["run"] is True
    assert called["agents"] == expected_agents


def test_run_hook_uninstall_only_removes_selected_agent(
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

    hook_setup.run_hook_uninstall(agents=["claude"])

    assert "hooks" not in json.loads(claude_path.read_text(encoding="utf-8"))
    assert "hooks" in json.loads(codex_path.read_text(encoding="utf-8"))


def test_hook_wizard_without_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["cyt", "hook", "all"])
    called: dict[str, bool | list[str] | None] = {"run": False, "agents": None}

    def fake_run_hook_setup(
        *,
        config_path: Path | None = None,
        agents: list[str] | None = None,
        prevent_hallucinations: bool = False,
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
        prevent_hallucinations: bool = False,
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


def test_upsert_cursor_hooks_into_file_writes_flat_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "hooks.json"
    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr(
        "cyt.hook.cli_invocation.cursor_hooks_dir",
        lambda: hooks_dir,
    )
    entries = hook_setup.cursor_hook_entries(agent="cursor")
    monkeypatch.setattr(
        "cyt.hook.setup_wizard.skills_hook_agent_interceptor_enabled",
        lambda _config=None: False,
    )

    changed = hook_setup.upsert_cursor_hooks_into_file(
        path,
        **hook_setup.cursor_upsert_hook_kwargs(entries, config={}),
    )

    assert changed is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert isinstance(data["hooks"], dict)
    assert data["hooks"]["beforeSubmitPrompt"] == [entries["before_submit"]]
    assert data["hooks"]["sessionStart"] == entries["session_start"]
    assert data["hooks"]["sessionEnd"] == [entries["session_end"]]
    assert data["hooks"]["preToolUse"] == [entries["pre_tool"]]
    assert data["hooks"]["postToolUse"] == [entries["post_tool"]]
    assert data["hooks"]["preCompact"] == [entries["pre_compact"]]
    if sys.platform == "win32":
        assert entries["before_submit"]["command"].endswith("cyt-client.cmd")
        assert entries["session_start"][0]["command"].endswith("cyt-hook-daemon-start.cmd")
    else:
        assert entries["before_submit"]["command"] == "cyt-client"
        assert entries["session_start"][0]["command"] == "cyt hook daemon start --unattended"


def test_upsert_cursor_hooks_installs_read_intercept_hooks_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "hooks.json"
    entries = hook_setup.cursor_hook_entries(agent="cursor")
    monkeypatch.setattr(
        "cyt.hook.setup_wizard.skills_hook_agent_interceptor_enabled",
        lambda _config=None: True,
    )

    changed = hook_setup.upsert_cursor_hooks_into_file(
        path,
        **hook_setup.cursor_upsert_hook_kwargs(entries, config={}),
    )

    assert changed is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["hooks"]["beforeReadFile"] == [entries["before_read_file"]]
    pre_tool_entries = data["hooks"]["preToolUse"]
    assert len(pre_tool_entries) == 2
    matchers = {entry.get("matcher") for entry in pre_tool_entries}
    assert matchers == {None, hook_setup.CURSOR_PRE_TOOL_READ_MATCHER}


def test_upsert_cursor_hooks_removes_legacy_mcp_tool_hook_events(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "preToolUse": [{"command": "cyt-client", "timeout": 60}],
                    "beforeMCPExecution": [{"command": "cyt-client", "timeout": 60}],
                    "afterMCPExecution": [
                        {
                            "command": "cyt-client",
                            "matcher": "cyt-mcp_get-tool-definitions|mcp__cyt-mcp__get-tool-definitions",
                            "timeout": 60,
                        },
                    ],
                },
            },
        ),
        encoding="utf-8",
    )
    entries = hook_setup.cursor_hook_entries(agent="cursor")

    changed = hook_setup.upsert_cursor_hooks_into_file(
        path,
        before_submit_entry=entries["before_submit"],
        session_start_entries=entries["session_start"],
        session_end_entry=entries["session_end"],
        pre_tool_entry=entries["pre_tool"],
        post_tool_entry=entries["post_tool"],
    )

    assert changed is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "beforeMCPExecution" not in data["hooks"]
    assert "afterMCPExecution" not in data["hooks"]
    assert data["hooks"]["postToolUse"] == [entries["post_tool"]]


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
        session_start_entries=hook_setup.cursor_session_start_entries(agent="cursor"),
        session_end_entry=hook_setup.cursor_session_end_entry(agent="cursor"),
        pre_tool_entry=hook_setup.cursor_before_submit_entry(agent="cursor"),
        post_tool_entry=hook_setup.cursor_hook_entries(agent="cursor")["post_tool"],
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
    hooks_dir = tmp_path / "cursor" / "hooks"
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_path)
    monkeypatch.setattr(
        "cyt.hook.cli_invocation.cursor_hooks_dir",
        lambda: hooks_dir,
    )
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(
        hook_setup,
        "_configure_hook_skills",
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
        patch("cyt.hook.daemon.daemon_start") as daemon_start,
    ):
        hook_setup.run_hook_setup(agents=["cursor"])

    daemon_start.assert_called_once_with(config_path=None, unattended=True)

    data = json.loads(cursor_path.read_text(encoding="utf-8"))
    client_cmd = data["hooks"]["beforeSubmitPrompt"][0]["command"]
    daemon_cmd = data["hooks"]["sessionStart"][0]["command"]
    session_client_cmd = data["hooks"]["sessionStart"][1]["command"]
    if sys.platform == "win32":
        assert client_cmd == str(hooks_dir / "cyt-client.cmd")
        assert daemon_cmd == str(hooks_dir / "cyt-hook-daemon-start.cmd")
        assert session_client_cmd == client_cmd
    else:
        assert client_cmd == "cyt-client"
        assert daemon_cmd == "cyt hook daemon start --unattended"
        assert session_client_cmd == "cyt-client"
    assert data["hooks"]["sessionEnd"][0]["command"] == session_client_cmd

    output = capsys.readouterr().out
    assert "\nStarting hook daemon via packaged cyt:" in output
    assert f"  {cyt_daemon_start_command()}" in output


def test_run_hook_setup_skips_cursor_daemon_start_when_declined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cursor_path = tmp_path / "cursor" / "hooks.json"
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(
        hook_setup,
        "_configure_hook_skills",
        lambda **_kwargs: None,
    )

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        lowered = text.lower()
        if "cyt_launch_agent" in lowered:
            return False
        if "start the hook daemon" in lowered:
            return False
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    _stub_tools_hook_wizard(monkeypatch)

    with (
        patch("cyt.hook.setup_wizard.load_config", return_value={"skills": {"enabled": True}}),
        patch("cyt.hook.daemon.daemon_start") as daemon_start,
    ):
        hook_setup.run_hook_setup(agents=["cursor"])

    daemon_start.assert_not_called()
    output = capsys.readouterr().out
    assert "Skipped. Run manually when ready:" in output
    assert f"  {cyt_daemon_start_command()}" in output


def test_run_hook_setup_prevent_hallucinations_skips_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cursor_path = tmp_path / "cursor" / "hooks.json"
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    prompt_calls: list[str] = []

    def fake_prompt_choice(text: str, *args: object, **kwargs: object) -> str:
        prompt_calls.append(text)
        if "choose action" in text:
            return "update"
        raise AssertionError(f"unexpected prompt: {text!r}")

    monkeypatch.setattr(hook_setup, "_prompt_choice", fake_prompt_choice)

    with (
        patch(
            "cyt.hook.setup_wizard.load_config",
            return_value={"skills": {"enabled": False}},
        ),
        patch("cyt.config.save_user_config", return_value=True),
        patch("cyt.config.sync_config_in_place"),
        patch("cyt.tools.cyt_mcp_setup.setup_cyt_mcp_for_agent") as setup_cyt_mcp,
        patch("cyt.hook.daemon.daemon_start") as daemon_start,
        patch(
            "cyt.hook.setup_wizard.resolve_setup_config_path",
            return_value=config_path,
        ),
    ):
        hook_setup.run_hook_setup(
            config_path=config_path,
            agents=["cursor"],
            prevent_hallucinations=True,
        )

    daemon_start.assert_called_once_with(config_path=config_path, unattended=True)
    setup_cyt_mcp.assert_called_once()
    setup_kwargs = setup_cyt_mcp.call_args.kwargs
    assert setup_kwargs["migrate_backends"] is True
    assert setup_kwargs["verify_only"] is True
    output = capsys.readouterr().out
    assert "CYT hook setup (cursor)" in output
    assert "Verify-only hallucination prevention enabled" in output
    assert "Skills directories" not in output
    assert "CYT_LAUNCH_AGENT" not in output
    assert "hook debug logging" not in output
    assert "\nStarting hook daemon for verify-only mode" in output
    assert "Run manually when ready:" not in output
    assert "Restart your agent so hook changes take effect." in output
    assert "Test the hook locally (beforeSubmitPrompt payload on stdin)" in output
    assert "cyt hook --uninstall" in output
    assert any("choose action (update | remove | skip)" in text for text in prompt_calls)

    data = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert "postToolUse" not in data.get("hooks", {})


def test_run_hook_setup_prevent_hallucinations_prompts_for_existing_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cursor_path = tmp_path / "cursor" / "hooks.json"
    config_path = tmp_path / "config.yaml"
    _write_duplicate_cursor_hooks(cursor_path)
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    prompt_calls = _stub_prevent_hallucinations_prompts(monkeypatch)

    with (
        patch(
            "cyt.hook.setup_wizard.load_config",
            return_value={"skills": {"enabled": False}},
        ),
        patch("cyt.config.save_user_config", return_value=True),
        patch("cyt.config.sync_config_in_place"),
        patch("cyt.tools.cyt_mcp_setup.setup_cyt_mcp_for_agent"),
        patch("cyt.hook.daemon.daemon_start"),
        patch(
            "cyt.hook.setup_wizard.resolve_setup_config_path",
            return_value=config_path,
        ),
    ):
        hook_setup.run_hook_setup(
            config_path=config_path,
            agents=["cursor"],
            prevent_hallucinations=True,
        )

    output = capsys.readouterr().out
    assert "CYT hooks" in output
    assert any("choose action (update | remove | skip)" in text for text in prompt_calls)
    assert "updated CYT hooks" in output
    assert "Test the hook locally (beforeSubmitPrompt payload on stdin)" in output

    data = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert "postToolUse" not in data.get("hooks", {})


def test_run_hook_setup_prevent_hallucinations_prompts_claude_inject_via(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude_path = tmp_path / "claude" / "settings.json"
    config_path = tmp_path / "config.yaml"
    claude_path.parent.mkdir(parents=True)
    claude_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(hook_setup, "CLAUDE_SETTINGS_PATH", claude_path)
    monkeypatch.setattr(hook_setup, "CODEX_HOOKS_PATH", tmp_path / "missing" / "hooks.json")
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", tmp_path / "missing" / "hooks.json")
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)
    saved: dict[str, Any] = {}
    prompt_calls: list[str] = []

    def capture_save(_path: Path, overlay: dict[str, Any], **kwargs: object) -> bool:
        del kwargs
        saved.update(overlay)
        return True

    def fake_prompt_choice(text: str, *args: object, **kwargs: object) -> str:
        prompt_calls.append(text)
        if "Detect tools for claude via (hook | proxy)" in text:
            return "hook"
        if "choose action" in text:
            return "update"
        raise AssertionError(f"unexpected prompt: {text!r}")

    monkeypatch.setattr(hook_setup, "_prompt_choice", fake_prompt_choice)
    monkeypatch.setattr(hook_setup, "_prompt_yes_no", lambda *_a, **_k: True)

    with (
        patch(
            "cyt.hook.setup_wizard.load_config",
            return_value={
                "skills": {"enabled": False},
                "pruning": {
                    "inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
                },
            },
        ),
        patch("cyt.config.save_user_config", side_effect=capture_save),
        patch("cyt.config.sync_config_in_place"),
        patch("cyt.tools.cyt_mcp_setup.setup_cyt_mcp_for_agent") as setup_cyt_mcp,
        patch("cyt.tools.cyt_mcp_setup.write_mcp_aggregator_yaml") as write_aggregator,
        patch(
            "cyt.hook.setup_wizard.resolve_setup_config_path",
            return_value=config_path,
        ),
    ):
        hook_setup.run_hook_setup(
            config_path=config_path,
            agents=["claude"],
            prevent_hallucinations=True,
        )

    output = capsys.readouterr().out
    assert "Tool detection (claude)" in output
    assert any("Detect tools for claude via (hook | proxy)" in text for text in prompt_calls)
    assert "Test the hook locally (preToolUse payload on stdin)" in output
    assert saved["hallucination_gate"]["enabled"] is True
    assert saved["pruning"]["inject_via"]["claude"] == "hook"
    setup_cyt_mcp.assert_called_once()
    assert setup_cyt_mcp.call_args.kwargs["verify_only"] is True
    write_aggregator.assert_not_called()


def test_apply_injection_hook_config_restores_cursor_rule_file_and_tools(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "hallucination_gate:",
                "  enabled: true",
                "skills:",
                "  enabled: false",
                "  hook:",
                "    cursor_rule_file:",
                "      enabled: false",
                "pruning:",
                "  tools:",
                "    enabled: false",
                "    hook:",
                "      tools_from: cyt_mcp",
                "  inject_via:",
                "    cursor: hook",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    saved_overlays: list[dict[str, object]] = []

    def capture_save(
        path: Path,
        overlay: dict[str, object],
        *,
        apply_bundled_sections: bool,
    ) -> bool:
        assert path == config_path
        assert apply_bundled_sections is False
        saved_overlays.append(overlay)
        return True

    config: dict[str, Any] = {
        "pruning": {"tools": {"hook": {"tools_from": ["cyt_mcp"]}}},
    }
    with (
        patch("cyt.config.save_user_config", side_effect=capture_save),
        patch("cyt.config.sync_config_in_place"),
    ):
        hook_setup._apply_injection_hook_config(
            config_path,
            config,
            agents=["cursor"],
        )

    assert saved_overlays == [
        {
            "hallucination_gate": {"enabled": False},
            "pruning": {"tools": {"enabled": True}},
        },
    ]


def test_apply_injection_hook_config_does_not_configure_mcp_aggregator(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config: dict[str, Any] = {
        "pruning": {
            "tools": {
                "enabled": False,
                "hook": {"tools_from": ["executor"]},
            },
            "inject_via": {"cursor": "hook"},
        },
    }
    with (
        patch("cyt.config.save_user_config", return_value=True),
        patch("cyt.config.sync_config_in_place"),
        patch("cyt.tools.cyt_mcp_setup.write_mcp_aggregator_yaml") as write_aggregator,
        patch("cyt.tools.cyt_mcp_setup.setup_cyt_mcp_for_agent") as setup_cyt_mcp,
    ):
        hook_setup._apply_injection_hook_config(
            config_path,
            config,
            agents=["cursor"],
        )

    write_aggregator.assert_not_called()
    setup_cyt_mcp.assert_not_called()


def test_configure_cursor_rule_file_prompts_and_saves_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "skills:\n  hook:\n    cursor_rule_file:\n      enabled: false\n",
        encoding="utf-8",
    )
    saved_overlays: list[dict[str, object]] = []

    def capture_save(
        path: Path,
        overlay: dict[str, object],
        *,
        apply_bundled_sections: bool,
    ) -> bool:
        saved_overlays.append(overlay)
        return True

    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(hook_setup, "_prompt_yes_no", lambda _text, *, default_yes=True: True)

    with (
        patch("cyt.config.save_user_config", side_effect=capture_save),
        patch("cyt.config.sync_config_in_place"),
    ):
        hook_setup._configure_cursor_rule_file(
            config_path,
            {
                "skills": {"enabled": True},
                "pruning": {"tools": {"enabled": False}},
            },
        )

    assert saved_overlays == [
        {"skills": {"hook": {"cursor_rule_file": {"enabled": True}}}},
    ]
    output = capsys.readouterr().out
    assert "Cursor rules file" in output
    assert "enabled" in output


def test_configure_cursor_rule_file_non_tty_defaults_to_enabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    saved_overlays: list[dict[str, object]] = []

    def capture_save(
        path: Path,
        overlay: dict[str, object],
        *,
        apply_bundled_sections: bool,
    ) -> bool:
        saved_overlays.append(overlay)
        return True

    with (
        patch.object(hook_setup.sys.stdin, "isatty", return_value=False),
        patch("cyt.config.save_user_config", side_effect=capture_save),
        patch("cyt.config.sync_config_in_place"),
    ):
        hook_setup._configure_cursor_rule_file(
            config_path,
            {"skills": {"enabled": True}, "pruning": {"tools": {"enabled": False}}},
        )

    assert saved_overlays == [
        {"skills": {"hook": {"cursor_rule_file": {"enabled": True}}}},
    ]


def test_configure_cursor_rule_file_auto_disables_without_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    saved_overlays: list[dict[str, object]] = []
    prompt_calls: list[str] = []

    def capture_save(
        path: Path,
        overlay: dict[str, object],
        *,
        apply_bundled_sections: bool,
    ) -> bool:
        saved_overlays.append(overlay)
        return True

    def fail_prompt(text: str, *, default_yes: bool = True) -> bool:
        prompt_calls.append(text)
        raise AssertionError(f"unexpected yes/no prompt: {text!r}")

    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fail_prompt)

    with (
        patch("cyt.config.save_user_config", side_effect=capture_save),
        patch("cyt.config.sync_config_in_place"),
    ):
        hook_setup._configure_cursor_rule_file(
            config_path,
            {"skills": {"enabled": False}, "pruning": {"tools": {"enabled": False}}},
        )

    assert saved_overlays == [
        {"skills": {"hook": {"cursor_rule_file": {"enabled": False}}}},
    ]
    assert prompt_calls == []
    output = capsys.readouterr().out
    assert "Cursor rules file" not in output
    assert "disabled" in output


def test_run_hook_setup_prevent_hallucinations_migrates_mcp_for_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import cyt.tools.cyt_mcp_setup as cyt_mcp_setup

    cursor_hooks_path = tmp_path / "cursor" / "hooks.json"
    mcp_source = tmp_path / "cursor" / "mcp.json"
    mcp_target_dir = tmp_path / "cyt_mcp"
    aggregator_path = tmp_path / "mcp-aggregator.yaml"
    config_path = tmp_path / "config.yaml"
    mcp_source.parent.mkdir(parents=True)
    mcp_source.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "example-backend": {"url": "https://mcp.example.com/mcp"},
                },
            },
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_hooks_path)
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", mcp_source)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", mcp_target_dir)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)
    yes_no_calls: list[str] = []

    def capture_yes_no(text: str, *args: object, **kwargs: object) -> bool:
        yes_no_calls.append(text)
        if "Configure workspace-scoped cyt-mcp" in text:
            return False
        if "Migrate agent MCP config" in text:
            return True
        if "choose action" in text.lower():
            return True
        raise AssertionError(f"unexpected yes/no prompt: {text!r}")

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", capture_yes_no)
    import cyt.hook.install_scope as install_scope

    monkeypatch.setattr(
        install_scope.CytInstallScope,
        "from_cwd",
        classmethod(lambda cls, *, cwd=None: install_scope.CytInstallScope(workspace_root=None)),
    )
    monkeypatch.setattr(hook_setup, "_prompt_choice", lambda text, *a, **k: "update")

    with (
        patch(
            "cyt.hook.setup_wizard.load_config",
            return_value={"skills": {"enabled": False}},
        ),
        patch("cyt.config.save_user_config", return_value=True),
        patch("cyt.config.sync_config_in_place"),
        patch("cyt.hook.daemon.daemon_start"),
        patch(
            "cyt.hook.setup_wizard.resolve_setup_config_path",
            return_value=config_path,
        ),
    ):
        hook_setup.run_hook_setup(
            config_path=config_path,
            agents=["cursor"],
            prevent_hallucinations=True,
        )

    output = capsys.readouterr()
    assert "--- Migrate (cursor)'s MCP config ---" in output.out
    assert any("Migrate agent MCP config" in text for text in yes_no_calls)
    assert "Detect tools for cursor" not in output.out
    backend_payload = json.loads((mcp_target_dir / "cursor.json").read_text(encoding="utf-8"))
    assert "example-backend" in backend_payload["mcpServers"]
    agent_payload = json.loads(mcp_source.read_text(encoding="utf-8"))
    assert set(agent_payload["mcpServers"]) == {"cyt-mcp"}
    assert "verify_only: true" in aggregator_path.read_text(encoding="utf-8")


def test_should_propose_hook_daemon_start() -> None:
    hook_config = {
        "pruning": {"inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"}},
    }
    hook_claude = {
        "pruning": {"inject_via": {"cursor": "hook", "claude": "hook", "codex": "proxy"}},
    }

    assert hook_setup._should_propose_hook_daemon_start(
        hook_config,
        ["cursor"],
        prevent_hallucinations=False,
    )
    assert hook_setup._should_propose_hook_daemon_start(
        hook_config,
        ["cursor"],
        prevent_hallucinations=True,
    )
    assert not hook_setup._should_propose_hook_daemon_start(
        hook_config,
        ["claude"],
        prevent_hallucinations=False,
    )
    assert hook_setup._should_propose_hook_daemon_start(
        hook_claude,
        ["claude"],
        prevent_hallucinations=False,
    )
    assert hook_setup._should_propose_hook_daemon_start(
        hook_config,
        ["claude", "codex", "cursor"],
        prevent_hallucinations=False,
    )


def test_run_hook_setup_skips_daemon_start_for_claude_proxy_inject_via(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "skills:",
                "  enabled: true",
                "pruning:",
                "  inject_via:",
                "    cursor: hook",
                "    claude: proxy",
                "    codex: proxy",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    claude_path = tmp_path / "claude" / "settings.json"
    claude_path.parent.mkdir(parents=True)
    claude_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(hook_setup, "CLAUDE_SETTINGS_PATH", claude_path)
    monkeypatch.setattr(hook_setup, "CODEX_HOOKS_PATH", tmp_path / "missing" / "hooks.json")
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", tmp_path / "missing" / "hooks.json")
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(
        hook_setup,
        "_configure_hook_skills",
        lambda **_kwargs: None,
    )
    _stub_tools_hook_wizard(monkeypatch)

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        if "Start the hook daemon" in text:
            raise AssertionError("daemon start should not be prompted for proxy inject_via")
        lowered = text.lower()
        if "debug" in lowered or "cyt_launch_agent" in lowered:
            return False
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)

    with patch("cyt.hook.daemon.daemon_start") as daemon_start:
        hook_setup.run_hook_setup(config_path=config_path, agents=["claude"])

    daemon_start.assert_not_called()
    output = capsys.readouterr().out
    assert "Start the hook daemon" not in output


def test_configure_hook_skills_skips_directories_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("skills:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)
    prompt_calls: list[str] = []

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        prompt_calls.append(text)
        if "Enable skills injection?" in text:
            return False
        raise AssertionError(f"unexpected yes/no prompt: {text!r}")

    def fail_prompt(text: str, default: str = "") -> str:
        raise AssertionError(f"unexpected prompt: {text!r}")

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    monkeypatch.setattr(hook_setup, "_prompt", fail_prompt)

    with patch("cyt.hook.setup_wizard.save_user_config", return_value=True):
        hook_setup._configure_hook_skills(
            config_path=config_path,
            include_claude=False,
            include_codex=False,
            include_cursor=True,
        )

    output = capsys.readouterr().out
    assert "Skills injection" in output
    assert "Skills directories" not in output
    assert any("Enable skills injection?" in text for text in prompt_calls)
    assert not any("Enable hook skill interceptor?" in text for text in prompt_calls)


def test_configure_hook_skills_prompts_interceptor_before_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("skills:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)
    prompt_calls: list[str] = []

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        prompt_calls.append(text)
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    monkeypatch.setattr(
        hook_setup,
        "_prompt_hook_skills_directories",
        lambda *_args, **_kwargs: ["~/.cursor/skills"],
    )
    monkeypatch.setattr(hook_setup, "_ensure_skill_directories_exist", lambda *_args: None)

    with patch("cyt.hook.setup_wizard.save_user_config", return_value=True):
        hook_setup._configure_hook_skills(
            config_path=config_path,
            include_claude=False,
            include_codex=False,
            include_cursor=True,
        )

    assert [call for call in prompt_calls if "Enable" in call or "Skills directories" in call] == [
        "Enable skills injection?",
        "Enable hook skill interceptor?",
    ]


def test_configure_hook_agent_interceptor_saves_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("skills:\n  enabled: true\n", encoding="utf-8")
    saved: dict[str, Any] = {}

    def capture_save(_path: Path, overlay: dict[str, Any], **kwargs: object) -> bool:
        del kwargs
        saved.update(overlay)
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", lambda *_args, **_kwargs: True)

    with patch("cyt.hook.setup_wizard.save_user_config", side_effect=capture_save):
        hook_setup._configure_hook_agent_interceptor(config_path)

    output = capsys.readouterr().out
    assert saved["skills"]["hook"]["agent_interceptor"]["enabled"] is True
    assert "agent_interceptor: enabled" in output


def test_save_tools_hook_wizard_config_skips_tool_sources_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    saved: dict[str, Any] = {}
    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)

    def fake_prompt_yes_no(text: str, *, default_yes: bool = True) -> bool:
        if "Enable tools injection?" in text:
            return False
        raise AssertionError(f"unexpected yes/no prompt: {text!r}")

    def fail_tools_prompt(*args: object, **kwargs: object) -> dict[str, Any]:
        raise AssertionError("tool hook config should not be prompted when tools are disabled")

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    monkeypatch.setattr(hook_setup, "prompt_tools_hook_config", fail_tools_prompt)

    def capture_save(_path: Path, overlay: dict[str, Any], **kwargs: object) -> bool:
        del kwargs
        saved.update(overlay)
        return True

    with (
        patch("cyt.hook.setup_wizard.save_user_config", side_effect=capture_save),
        patch(
            "cyt.hook.setup_wizard.load_config",
            return_value={"pruning": {"tools": {"enabled": True}}},
        ),
        patch("cyt.hook.setup_wizard.load_user_config_overlay", return_value={}),
    ):
        hook_setup._save_tools_hook_wizard_config(
            config_path,
            {"pruning": {"tools": {"enabled": True}}},
            config_path=config_path,
        )

    output = capsys.readouterr().out
    assert "Tools injection" in output
    assert "MCP aggregator" not in output
    assert "Tool hook injection" not in output
    assert saved["pruning"]["tools"]["enabled"] is False


def test_install_windows_hook_wrappers_writes_cmd_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.hook import cli_invocation as hook_cli

    hooks_dir = tmp_path / "cursor" / "hooks"
    monkeypatch.setattr(hook_cli, "cursor_hooks_dir", lambda: hooks_dir)
    monkeypatch.setattr(hook_cli, "use_windows_hook_wrappers", lambda *, invocation=None: True)
    repo_root = Path(r"C:\Users\me\git\clear-your-tools")
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)

    wrappers = hook_cli.install_windows_hook_wrappers(invocation=invocation)

    assert wrappers["client"].is_file()
    assert wrappers["daemon_start"].is_file()
    client_text = wrappers["client"].read_text(encoding="utf-8")
    daemon_text = wrappers["daemon_start"].read_text(encoding="utf-8")
    assert " run --directory " in client_text
    assert r"C:\Users\me\git\clear-your-tools" in client_text
    if sys.platform == "win32":
        assert "'" not in client_text
        assert "'" not in daemon_text
        assert '--directory "' in client_text
        assert '--directory "' in daemon_text
    assert hook_cli.is_windows_hook_wrapper_command(str(wrappers["client"]))
    assert hook_cli.is_dev_cyt_hook_command(str(wrappers["client"]))


def test_upsert_cursor_hooks_dev_mode_uses_windows_wrappers_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.hook import cli_invocation as hook_cli

    repo_root = Path(r"C:\Users\me\git\clear-your-tools")
    invocation = HookCliInvocation(mode="dev", repo_root=repo_root)
    hooks_dir = tmp_path / "cursor" / "hooks"
    monkeypatch.setattr(hook_cli, "cursor_hooks_dir", lambda: hooks_dir)

    inline_client = build_uv_run_dev_command(
        repo_root,
        cyt_client_cli_script_relpath(),
    )
    inline_daemon = build_uv_run_dev_command(
        repo_root,
        proxy_cli_script_relpath(),
        "hook",
        "daemon",
        "start",
        "--unattended",
    )
    existing = {
        hook_setup.CURSOR_SESSION_START_EVENT: [
            {"type": "command", "command": inline_daemon, "timeout": 60},
            {"type": "command", "command": inline_client, "timeout": 60},
        ],
        hook_setup.CURSOR_BEFORE_SUBMIT_EVENT: [
            {"type": "command", "command": inline_client, "timeout": 60},
        ],
    }
    entries = hook_setup.cursor_hook_entries(agent="cursor", invocation=invocation)
    merged, changed = hook_setup.upsert_cursor_hooks(
        existing,
        before_submit_entry=entries["before_submit"],
        session_start_entries=entries["session_start"],
        session_end_entry=entries["session_end"],
        pre_tool_entry=entries["pre_tool"],
        post_tool_entry=entries["post_tool"],
        pre_compact_entry=entries["pre_compact"],
    )

    session_commands = [
        entry["command"]
        for entry in merged[hook_setup.CURSOR_SESSION_START_EVENT]
        if isinstance(entry, dict)
    ]
    before_submit_command = merged[hook_setup.CURSOR_BEFORE_SUBMIT_EVENT][0]["command"]
    if sys.platform == "win32":
        assert before_submit_command.endswith("cyt-client-dev.cmd")
        assert any(
            str(command).endswith("cyt-hook-daemon-start-dev.cmd") for command in session_commands
        )
        assert any(str(command).endswith("cyt-client-dev.cmd") for command in session_commands)
        client_wrapper = Path(before_submit_command)
        assert client_wrapper.is_file()
        assert " run --directory " in client_wrapper.read_text(encoding="utf-8")
    else:
        assert inline_client in session_commands
        assert inline_daemon in session_commands
        assert before_submit_command == inline_client
    _ = changed


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only env prefix syntax")
def test_prefix_command_env_windows_uses_cmd_c() -> None:
    from cyt.hook.cli_invocation import prefix_command_env

    command = prefix_command_env({"CYT_LAUNCH_AGENT": "cursor"}, r"C:\hooks\cyt-client.cmd")
    assert command.startswith('cmd /c "set "CYT_LAUNCH_AGENT=cursor"&& call "')
    assert "cyt-client.cmd" in command


def test_is_cyt_hook_command_recognizes_windows_wrapper() -> None:
    assert hook_setup._is_cyt_hook_command(r"C:\Users\me\.cursor\hooks\cyt-client-dev.cmd")

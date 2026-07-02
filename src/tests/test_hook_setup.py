"""Tests for `cyt hook` setup wizard."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.skills import hook_setup


def _stub_tools_hook_wizard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hook_setup,
        "prompt_tools_hook_config",
        lambda existing, *, context: {
            "tools": {
                "inject_via": "proxy",
                "hook": {
                    "tools_from": "client",
                    "mcp_client_file": "~/.config/cyt/mcp.json",
                    "mcp_definitions_file": "~/.config/cyt/mcp-definitions.json",
                },
            },
        },
    )
    monkeypatch.setattr(hook_setup, "save_user_config", lambda *args, **kwargs: False)


def test_format_hook_stdin_test_command_uses_anonymized_payload() -> None:
    command = hook_setup.format_hook_stdin_test_command()

    assert "cat <<'EOF' | cyt hook --stdin" in command
    assert "019ebcaf" not in command
    assert "dberezenko" not in command
    assert "sess-00000000-0000-4000-8000-000000000001" in command
    assert "/Users/you/.codex/sessions/2026/06/12/rollout-example.jsonl" in command
    assert "/path/to/your/project" in command


def test_format_hook_stdin_test_command_includes_debug_flag() -> None:
    command = hook_setup.format_hook_stdin_test_command(debug=True)

    assert "cyt hook --stdin --debug" in command


def test_cyt_hook_entry_sets_launch_agent_env_for_agent_specific_hooks() -> None:
    claude_entry = hook_setup.cyt_hook_entry(agent="claude")
    codex_entry = hook_setup.cyt_hook_entry(agent="codex", debug=True)

    assert claude_entry["command"] == "CYT_LAUNCH_AGENT=claude cyt hook --stdin"
    assert codex_entry["command"] == "CYT_LAUNCH_AGENT=codex cyt hook --stdin --debug"
    assert claude_entry["timeout"] == hook_setup.HOOK_TIMEOUT_SECONDS
    assert codex_entry["timeout"] == hook_setup.HOOK_TIMEOUT_SECONDS


def test_is_cyt_hook_command_recognizes_launch_agent_prefix() -> None:
    assert hook_setup._is_cyt_hook_command("CYT_LAUNCH_AGENT=claude cyt hook --stdin")
    assert hook_setup._is_cyt_hook_command("CYT_LAUNCH_AGENT=codex cyt hook --stdin --debug")
    assert not hook_setup._is_cyt_hook_command("/usr/local/bin/other-hook")


def test_merge_cyt_hook_skips_env_prefixed_existing_command() -> None:
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
    entry = hook_setup.cyt_hook_entry(agent="claude", debug=False)
    merged, changed = hook_setup.merge_cyt_hook(existing, entry)

    assert changed is False
    assert merged == existing


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
    entry = hook_setup.cyt_hook_entry(agent="claude", debug=False)
    merged, changed = hook_setup.upsert_cyt_hook(existing, entry)

    assert changed is True
    hooks = merged["UserPromptSubmit"][0]["hooks"]
    assert len(hooks) == 2
    commands = [hook["command"] for hook in hooks]
    assert "CYT_LAUNCH_AGENT=claude cyt hook --stdin" in commands
    assert "/usr/local/bin/other-hook" in commands


def test_upsert_cyt_hook_is_noop_when_settings_match() -> None:
    existing = {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "CYT_LAUNCH_AGENT=codex cyt hook --stdin --debug",
                        "timeout": 60,
                    },
                ],
            },
        ],
    }
    entry = hook_setup.cyt_hook_entry(agent="codex", debug=True)
    merged, changed = hook_setup.upsert_cyt_hook(existing, entry)

    assert changed is False
    assert merged == existing


def test_merge_cyt_hook_adds_user_prompt_submit_entry() -> None:
    entry = hook_setup.cyt_hook_entry()
    merged, changed = hook_setup.merge_cyt_hook({}, entry)

    assert changed is True
    assert merged["UserPromptSubmit"][0]["hooks"][0]["command"] == "cyt hook --stdin"


def test_merge_cyt_hook_skips_existing_cyt_hook_command() -> None:
    existing = {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {"type": "command", "command": "cyt hook --stdin --debug", "timeout": 60},
                ],
            },
        ],
    }
    entry = hook_setup.cyt_hook_entry(debug=False)
    merged, changed = hook_setup.merge_cyt_hook(existing, entry)

    assert changed is False
    assert merged == existing


def test_merge_cyt_hook_skips_legacy_cyt_skills_command() -> None:
    existing = {
        "UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "cyt skills", "timeout": 60}]},
        ],
    }
    entry = hook_setup.cyt_hook_entry()
    merged, changed = hook_setup.merge_cyt_hook(existing, entry)

    assert changed is False
    assert merged == existing


def test_merge_hooks_into_file_writes_claude_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"env":{"FOO":"bar"}}\n', encoding="utf-8")
    entry = hook_setup.cyt_hook_entry()

    changed = hook_setup.merge_hooks_into_file(path, entry)

    assert changed is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["env"]["FOO"] == "bar"
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "cyt hook --stdin"


def test_merge_hooks_into_file_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    entry = hook_setup.cyt_hook_entry()

    assert hook_setup.merge_hooks_into_file(path, entry) is True
    assert hook_setup.merge_hooks_into_file(path, entry) is False


def test_build_hook_skills_config_overlay_returns_none_when_already_configured() -> None:
    overlay = hook_setup.build_hook_skills_config_overlay(
        {
            "enabled": True,
            "inject_via": "hook",
            "directories": ["~/.claude/skills", "~/.codex/skills"],
        },
        ["~/.claude/skills", "~/.codex/skills"],
    )

    assert overlay is None


def test_build_hook_skills_config_overlay_updates_inject_via_from_proxy() -> None:
    overlay = hook_setup.build_hook_skills_config_overlay(
        {
            "pipeline": "llm",
            "enabled": True,
            "inject_via": "proxy",
        },
        ["~/.claude/skills"],
    )

    assert overlay == {
        "skills": {
            "enabled": True,
            "inject_via": "hook",
            "directories": ["~/.claude/skills"],
        },
    }


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

    assert claude_only == ["~/.claude/skills"]
    assert both == ["~/.claude/skills", "~/.codex/skills"]


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
    assert "inject_via: hook" in text


def test_save_hook_skills_directories_preserves_pipeline_and_updates_inject_via(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "skills:\n  pipeline: llm\n  inject_via: proxy\n  enabled: true\n",
        encoding="utf-8",
    )
    user_overlay = {"skills": {"pipeline": "llm", "inject_via": "proxy", "enabled": True}}

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
        "  inject_via: hook\n"
        "  enabled: true\n"
        "  directories:\n"
        "    - ~/.claude/skills\n"
        "    - ~/.codex/skills\n",
        encoding="utf-8",
    )
    user_overlay = {
        "skills": {
            "pipeline": "llm",
            "inject_via": "hook",
            "enabled": True,
            "directories": ["~/.claude/skills", "~/.codex/skills"],
        },
    }

    changed = hook_setup._save_hook_skills_directories(
        config_path,
        ["~/.claude/skills", "~/.codex/skills"],
        user_overlay=user_overlay,
    )

    assert changed is False
    assert config_path.read_text(encoding="utf-8") == (
        "skills:\n"
        "  pipeline: llm\n"
        "  inject_via: hook\n"
        "  enabled: true\n"
        "  directories:\n"
        "    - ~/.claude/skills\n"
        "    - ~/.codex/skills\n"
    )


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
        if "debug" in text.lower():
            return False
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    monkeypatch.setattr(hook_setup, "_prompt_choice", lambda *_args, **_kwargs: "update")
    _stub_tools_hook_wizard(monkeypatch)

    with patch("cyt.skills.hook_setup.load_config", return_value={"skills": {"enabled": True}}):
        hook_setup.run_hook_setup()

    claude_data = json.loads(claude_path.read_text(encoding="utf-8"))
    hooks = claude_data["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert len(hooks) == 1
    assert hooks[0]["command"] == "CYT_LAUNCH_AGENT=claude cyt hook --stdin"


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
        if "debug" in text.lower():
            return False
        return True

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", fake_prompt_yes_no)
    _stub_tools_hook_wizard(monkeypatch)

    with patch("cyt.skills.hook_setup.load_config", return_value={"skills": {"enabled": True}}):
        hook_setup.run_hook_setup()

    claude_data = json.loads(claude_path.read_text(encoding="utf-8"))
    codex_data = json.loads(codex_path.read_text(encoding="utf-8"))
    assert (
        claude_data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        == "CYT_LAUNCH_AGENT=claude cyt hook --stdin"
    )
    assert (
        codex_data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        == "CYT_LAUNCH_AGENT=codex cyt hook --stdin"
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

    with patch("cyt.skills.hook_setup.load_config", return_value={"skills": {"enabled": True}}):
        hook_setup.run_hook_setup()

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
        with patch("cyt.skills.hook_setup.load_config", return_value={"skills": {"enabled": True}}):
            hook_setup.run_hook_setup()

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
    entry = hook_setup.cyt_hook_entry()
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

    monkeypatch.setattr("cyt.skills.hook_setup.run_hook_uninstall", fake_run_hook_uninstall)

    from cyt.proxy.cli_impl import main

    main()
    assert called["run"] is True


def test_hook_wizard_without_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["cyt", "hook"])
    called = {"run": False}

    def fake_run_hook_setup(*, config_path: Path | None = None) -> None:
        called["run"] = True
        assert config_path is None

    monkeypatch.setattr("cyt.skills.hook_setup.run_hook_setup", fake_run_hook_setup)

    from cyt.proxy.cli_impl import main

    main()
    assert called["run"] is True

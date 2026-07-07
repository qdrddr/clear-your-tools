"""Tests for hook-injection launch behavior."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cyt.agents.claude.launch import build_claude_env
from cyt.agents.codex.launch import (
    PROVIDER_NAME,
    build_switch_provider_codex_args,
    hook_mode_codex_launch_args,
    read_config_model_provider,
)
from cyt.agents.codex.launch import (
    run as run_codex,
)
from cyt.launch.agent_credentials import AgentAuthBinding
from cyt.launch.env_report import print_runtime_env_report


def _openrouter_upstream() -> dict[str, str]:
    return {
        "endpoint": "openrouter",
        "kind": "openrouter",
        "url": "https://openrouter.ai/api",
        "provider_nick": "openrouter-ai",
    }


def _openrouter_api_key_var() -> str:
    return "OPENROUTER_" + "API_KEY"


def _config(*, inject_via: str = "hook") -> dict[str, Any]:
    return {
        "pruning": {"inject_via": inject_via},
        "skills": {"enabled": True},
        "network": {
            "proxy": {
                "reverse": {
                    "upstreams": [_openrouter_upstream()],
                },
            },
        },
    }


class TestHookModeClaudeEnv:
    def test_hook_mode_leaves_anthropic_env_unset(self) -> None:
        env, reportable = build_claude_env(
            config=_config(),
            port=8834,
            endpoint="openrouter",
            use_proxy=False,
            switch_provider=False,
        )
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert reportable == {}

    def test_switch_provider_sets_direct_upstream(self) -> None:
        binding = AgentAuthBinding(
            agent_env_var="ANTHROPIC_AUTH_TOKEN",
            source=f"via {_openrouter_api_key_var()}",
            token="or-token",
            upstream_key_var=_openrouter_api_key_var(),
        )
        env, reportable = build_claude_env(
            config=_config(),
            port=8834,
            endpoint="openrouter",
            auth_binding=binding,
            use_proxy=False,
            switch_provider=True,
        )
        assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "or-token"
        assert reportable["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"


class TestHookModeCodexArgs:
    def test_overrides_cyt_provider_to_openai(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        codex_path = tmp_path / "config.toml"
        codex_path.write_text('model_provider = "cyt"\n', encoding="utf-8")
        monkeypatch.setattr("cyt.agents.codex.launch.CODEX_CONFIG_PATH", codex_path)
        assert read_config_model_provider() == PROVIDER_NAME
        assert hook_mode_codex_launch_args() == ["-c", 'model_provider="openai"']

    def test_no_override_when_provider_not_cyt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        codex_path = tmp_path / "config.toml"
        codex_path.write_text('model_provider = "openai"\n', encoding="utf-8")
        monkeypatch.setattr("cyt.agents.codex.launch.CODEX_CONFIG_PATH", codex_path)
        assert hook_mode_codex_launch_args() == []

    def test_switch_provider_uses_direct_upstream(self) -> None:
        args = build_switch_provider_codex_args(
            config=_config(),
            endpoint="openrouter",
            env_key="CODEX_OPENAI_API_KEY",
        )
        joined = " ".join(args)
        assert 'model_provider="openrouter-ai"' in joined
        assert "https://openrouter.ai/api/v1" in joined
        assert 'env_key="CODEX_OPENAI_API_KEY"' in joined

    def test_hook_mode_run_adds_provider_override(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        codex_path = tmp_path / "config.toml"
        codex_path.write_text('model_provider = "cyt"\n', encoding="utf-8")
        monkeypatch.setattr("cyt.agents.codex.launch.CODEX_CONFIG_PATH", codex_path)
        captured: dict[str, list[str]] = {}

        def fake_run(
            cmd: list[str],
            *,
            env: dict[str, str],
            check: bool,
        ) -> subprocess.CompletedProcess[str]:
            del env, check
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr("cyt.agents.codex.launch.subprocess.run", fake_run)
        monkeypatch.setattr("cyt.agents.codex.launch.find_codex", lambda: "codex")

        result = run_codex(
            config=_config(),
            port=8834,
            endpoint="openrouter",
            agent_args=["-m", "gpt-5.4-mini"],
            use_proxy=False,
            switch_provider=False,
        )

        assert result == 0
        assert captured["cmd"] == [
            "codex",
            "-m",
            "gpt-5.4-mini",
            "-c",
            'model_provider="openai"',
        ]

    def test_hook_mode_run_skips_auth_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_key = "CODEX_OPENAI_API_KEY"
        monkeypatch.delenv(env_key, raising=False)
        binding = AgentAuthBinding(
            agent_env_var=env_key,
            source=f"via {_openrouter_api_key_var()}",
            token="codex-token",
            upstream_key_var=_openrouter_api_key_var(),
        )
        captured: dict[str, dict[str, str]] = {}

        def fake_run(
            cmd: list[str],
            *,
            env: dict[str, str],
            check: bool,
        ) -> subprocess.CompletedProcess[str]:
            del cmd, check
            captured["env"] = env
            return subprocess.CompletedProcess(args=[], returncode=0)

        monkeypatch.setattr("cyt.agents.codex.launch.subprocess.run", fake_run)
        monkeypatch.setattr("cyt.agents.codex.launch.find_codex", lambda: "codex")

        run_codex(
            config=_config(),
            port=8834,
            endpoint="openrouter",
            agent_args=[],
            auth_binding=binding,
            use_proxy=False,
            switch_provider=False,
        )
        assert env_key not in captured["env"]


class TestHookModeEnvReport:
    def test_hook_mode_recipe_omits_proxy_urls(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        print_runtime_env_report(
            quiet=False,
            credential_sources={},
            port=8834,
            endpoint="openrouter",
            upstream_url="https://openrouter.ai/api",
            include_agent_recipe=True,
            agent="codex",
            launch_env={"CYT_HOOK_URL": "http://127.0.0.1:8834/hook/inject"},
            config=_config(),
            hook_mode=True,
            switch_provider=False,
        )
        err = capsys.readouterr().err
        assert "Hook server:" in err
        assert "Manual hook recipe:" in err
        assert "cyt hook daemon start" in err
        assert "Manual proxy recipe" not in err
        assert "model_providers.cyt.base_url" not in err
        assert 'model_provider="openai"' in err

    def test_switch_provider_recipe_shows_direct_upstream(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        binding = AgentAuthBinding(
            agent_env_var="ANTHROPIC_AUTH_TOKEN",
            source=f"via {_openrouter_api_key_var()}",
            token="or-token",
            upstream_key_var=_openrouter_api_key_var(),
        )
        print_runtime_env_report(
            quiet=False,
            credential_sources={_openrouter_api_key_var(): "env: shell"},
            port=8834,
            endpoint="openrouter",
            upstream_url="https://openrouter.ai/api",
            include_agent_recipe=True,
            agent="claude",
            config=_config(),
            auth_binding=binding,
            hook_mode=True,
            switch_provider=True,
        )
        err = capsys.readouterr().err
        assert 'export ANTHROPIC_BASE_URL="https://openrouter.ai/api"' in err
        assert "localhost:8834/openrouter" not in err.split("Manual agent recipe")[-1]


def test_run_launch_session_starts_hook_server_when_not_proxy() -> None:
    from cyt.launch import cli as launch_cli

    runtime = MagicMock()
    runtime.config = _config()
    runtime.config_path = MagicMock()
    runtime.port = 8787
    runtime.credential_sources = {}
    runtime.upstream_url = None

    args = MagicMock()
    args.debug = False
    args.debug_dry_run = False
    args.debug_strict = False
    args.switch_provider = False
    args.proxy = False

    with (
        patch.object(launch_cli, "sys") as mock_sys,
        patch.object(launch_cli, "ensure_tools_hook_file_interactive", side_effect=lambda _p, c: c),
        patch.object(launch_cli, "ensure_proxy") as ensure_proxy,
        patch.object(launch_cli, "require_healthy_proxy") as require_healthy,
        patch.object(launch_cli, "_ensure_hook_server") as ensure_hook,
        patch.object(launch_cli, "_ensure_launch_agent_auth") as ensure_auth,
        patch.object(launch_cli, "print_runtime_env_report"),
        patch.object(launch_cli, "_run_launched_agent", return_value=0),
        patch.object(launch_cli, "_launch_debug_flags", return_value=(False, False, False)),
        patch.object(launch_cli, "launch_agent_env", return_value={}),
    ):
        mock_sys.stdin.isatty.return_value = False
        launch_cli._run_launch_session(
            args=args,
            agent="claude",
            agent_args=[],
            runtime=runtime,
            endpoint="openrouter",
        )

    ensure_proxy.assert_not_called()
    require_healthy.assert_not_called()
    ensure_hook.assert_called_once()
    ensure_auth.assert_not_called()


def test_switch_provider_requires_hook_mode() -> None:
    from cyt.launch import cli as launch_cli

    runtime = MagicMock()
    runtime.config = {"pruning": {"inject_via": "proxy"}}
    runtime.config_path = MagicMock()
    runtime.port = 8787
    runtime.credential_sources = {}

    args = MagicMock()
    args.debug = False
    args.debug_dry_run = False
    args.debug_strict = False
    args.switch_provider = True
    args.proxy = False

    with (
        patch.object(launch_cli, "sys") as mock_sys,
        patch.object(launch_cli, "ensure_tools_hook_file_interactive", side_effect=lambda _p, c: c),
        patch.object(launch_cli, "launch_agent_env", return_value={}),
        patch.object(launch_cli, "_launch_debug_flags", return_value=(False, False, False)),
    ):
        mock_sys.stdin.isatty.return_value = False
        with pytest.raises(SystemExit, match="--switch-provider is only supported"):
            launch_cli._run_launch_session(
                args=args,
                agent="claude",
                agent_args=[],
                runtime=runtime,
                endpoint="openrouter",
            )

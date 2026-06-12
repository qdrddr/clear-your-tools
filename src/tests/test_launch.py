"""Tests for ``cyt launch`` and shared runtime bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

import cyt.config as configs
from cyt.launch.cli import parse_launch_remainder
from cyt.launch.cli import run as run_launch
from cyt.launch.codex import (
    MANAGED_END,
    MANAGED_START,
    configure_provider,
    ensure_provider_configured,
    managed_provider_base_url,
    restore_provider,
    validate_agent_args,
)
from cyt.launch.config import required_launch_env_var_names
from cyt.launch.endpoints import resolve_agent_endpoint
from cyt.launch.env_report import print_runtime_env_report
from cyt.launch.proxy_guard import ensure_proxy
from cyt.launch.secrets import ensure_runtime_credentials
from cyt.launch.upstream import (
    filter_upstreams_by_agent,
    infer_upstream_kind_from_url,
    resolve_upstream_kind,
    select_upstream_endpoint,
)
from cyt.proxy.bootstrap import prepare_runtime
from cyt.proxy.setup import apply_upstream_cli_to_config


def _anthropic_api_key_var() -> str:
    return "ANTHROPIC_" + "API_KEY"


def _codex_openai_api_key_var() -> str:
    return "CODEX_OPENAI_" + "API_KEY"


def _openai_api_key_var() -> str:
    return "OPENAI_" + "API_KEY"


def _openrouter_api_key_var() -> str:
    return "OPENROUTER_" + "API_KEY"


@pytest.fixture
def isolated_config_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    user_config = tmp_path / "home" / ".config" / "cyt" / "config.yaml"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(configs, "DEFAULT_USER_CONFIG_PATH", user_config)
    monkeypatch.chdir(work_dir)
    return {"user_config": user_config}


def _write_upstream_config(
    path: Path,
    *,
    name: str,
    kind: str,
    url: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            {
                "network": {
                    "proxy": {
                        "reverse": {
                            "upstreams": [
                                {"upstream": name, "kind": kind, "url": url},
                            ],
                            "endpoints": [name],
                        },
                    },
                },
            },
            default_flow_style=False,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


class TestFilterUpstreamsByAgent:
    def test_claude_keeps_anthropic_only(self) -> None:
        upstreams = [
            {"upstream": "openai", "kind": "openai", "url": "https://api.openai.com"},
            {"upstream": "anthropic", "kind": "anthropic", "url": "https://api.anthropic.com"},
            {
                "upstream": "openrouter",
                "kind": "anthropic",
                "url": "https://openrouter.ai/api",
            },
        ]
        names = [entry["upstream"] for entry in filter_upstreams_by_agent(upstreams, "claude")]
        assert names == ["anthropic", "openrouter"]

    def test_codex_keeps_openai_only(self) -> None:
        upstreams = [
            {"upstream": "openai", "kind": "openai", "url": "https://api.openai.com"},
            {"upstream": "anthropic", "kind": "anthropic", "url": "https://api.anthropic.com"},
        ]
        names = [entry["upstream"] for entry in filter_upstreams_by_agent(upstreams, "codex")]
        assert names == ["openai"]


class TestSelectUpstreamEndpoint:
    def test_auto_selects_single_match(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        upstreams = [
            {"upstream": "openai", "kind": "openai", "url": "https://api.openai.com"},
            {"upstream": "anthropic", "kind": "anthropic", "url": "https://api.anthropic.com"},
        ]
        endpoint = select_upstream_endpoint(
            upstreams,
            agent="claude",
            label="ignored",
        )
        assert endpoint == "anthropic"
        err = capsys.readouterr().err
        assert "Auto-selected upstream for claude (kind=anthropic)" in err
        assert "anthropic (https://api.anthropic.com)" in err

    def test_prompts_when_multiple_matches(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        upstreams = [
            {"upstream": "anthropic", "kind": "anthropic", "url": "https://api.anthropic.com"},
            {
                "upstream": "openrouter",
                "kind": "anthropic",
                "url": "https://openrouter.ai/api",
            },
        ]
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(
            "cyt.launch.upstream.prompt_upstream_picker",
            lambda compatible, **kwargs: "openrouter",
        )
        endpoint = select_upstream_endpoint(
            upstreams,
            agent="claude",
            label="Select upstream endpoint for this launch",
        )
        assert endpoint == "openrouter"


class TestResolveAgentEndpoint:
    def test_auto_selects_from_merged_config(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = {
            "network": {
                "proxy": {
                    "reverse": {
                        "upstreams": [
                            {
                                "upstream": "openai",
                                "kind": "openai",
                                "url": "https://api.openai.com",
                            },
                            {
                                "upstream": "anthropic",
                                "kind": "anthropic",
                                "url": "https://api.anthropic.com",
                            },
                        ],
                    },
                },
            },
        }
        endpoint = resolve_agent_endpoint(
            config,
            agent="claude",
            config_path=None,
            endpoint_override=None,
            upstream_cli_endpoint=None,
        )
        assert endpoint == "anthropic"
        err = capsys.readouterr().err
        assert "Auto-selected upstream for claude (kind=anthropic)" in err


class TestResolveUpstreamKind:
    def test_canonical_openai_url(self) -> None:
        assert infer_upstream_kind_from_url("https://api.openai.com") == "openai"

    def test_canonical_anthropic_url(self) -> None:
        assert infer_upstream_kind_from_url("https://api.anthropic.com/") == "anthropic"

    def test_openrouter_requires_explicit_kind(self) -> None:
        assert (
            resolve_upstream_kind(
                "https://openrouter.ai/api",
                agent=None,
                explicit=None,
            )
            is None
        )

    def test_agent_fallback_for_launch(self) -> None:
        assert resolve_upstream_kind(None, agent="claude", explicit=None) == "anthropic"
        assert resolve_upstream_kind(None, agent="codex", explicit=None) == "openai"

    def test_explicit_kind_wins(self) -> None:
        assert (
            resolve_upstream_kind(
                "https://openrouter.ai/api",
                agent="codex",
                explicit="anthropic",
            )
            == "anthropic"
        )


class TestApplyUpstreamCli:
    def test_idempotent_no_op(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        apply_upstream_cli_to_config(
            config_path,
            upstream_url="https://api.openai.com",
            upstream_kind="openai",
        )
        first = config_path.read_text(encoding="utf-8")
        apply_upstream_cli_to_config(
            config_path,
            upstream_url="https://api.openai.com",
            upstream_kind="openai",
        )
        assert config_path.read_text(encoding="utf-8") == first

    def test_update_url(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        apply_upstream_cli_to_config(
            config_path,
            upstream_url="https://api.openai.com",
            upstream_kind="openai",
        )
        apply_upstream_cli_to_config(
            config_path,
            upstream_url="https://api.openai.com/v1",
            upstream_kind="openai",
        )
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        url = saved["network"]["proxy"]["reverse"]["upstreams"][0]["url"]
        assert url == "https://api.openai.com/v1"


class TestParseLaunchRemainder:
    def test_requires_separator(self) -> None:
        with pytest.raises(SystemExit, match="requires `--`"):
            parse_launch_remainder(["claude"])

    def test_parses_agent_and_args(self) -> None:
        agent, args = parse_launch_remainder(["--", "codex", "-m", "gpt-5.4-mini"])
        assert agent == "codex"
        assert args == ["-m", "gpt-5.4-mini"]


class TestRequiredLaunchEnvVars:
    def test_codex_requires_codex_key(self, isolated_config_paths: dict[str, Path]) -> None:
        names = required_launch_env_var_names({}, "codex")
        assert _codex_openai_api_key_var() in names

    def test_claude_does_not_require_upstream_keys(
        self,
        isolated_config_paths: dict[str, Path],
    ) -> None:
        config = {
            "network": {
                "proxy": {
                    "reverse": {
                        "upstreams": [
                            {
                                "upstream": "anthropic",
                                "kind": "anthropic",
                                "url": "https://api.anthropic.com",
                            },
                        ],
                    },
                },
            },
        }
        names = required_launch_env_var_names(config, "claude")
        assert _anthropic_api_key_var() not in names
        assert _openrouter_api_key_var() not in names
        assert _codex_openai_api_key_var() not in names

    def test_claude_requires_tool_pruner_keys_only(
        self,
        isolated_config_paths: dict[str, Path],
    ) -> None:
        config = {
            "pruning": {
                "pipeline": ["rerank"],
                "rerank": {"model": {"remote": {"model_nick": "rerank-qwen3-8b"}}},
            },
            "models": {
                "rerankers": {
                    "remote": [
                        {
                            "nick": "rerank-qwen3-8b",
                            "key_var_name": "DEEPINFRA_API_KEY",
                        },
                    ],
                },
            },
        }
        names = required_launch_env_var_names(config, "claude")
        assert names == ["DEEPINFRA_API_KEY"]
        assert _anthropic_api_key_var() not in names

    def test_claude_endpoint_does_not_add_upstream_keys(
        self,
        isolated_config_paths: dict[str, Path],
    ) -> None:
        config = {
            "network": {
                "proxy": {
                    "reverse": {
                        "upstreams": [
                            {
                                "upstream": "anthropic",
                                "kind": "anthropic",
                                "url": "https://api.anthropic.com",
                            },
                            {
                                "upstream": "openrouter",
                                "kind": "anthropic",
                                "url": "https://openrouter.ai/api",
                            },
                        ],
                    },
                },
            },
            "pruning": {"pipeline": ["bm25"]},
        }
        names = required_launch_env_var_names(
            config,
            "claude",
            endpoint="anthropic",
        )
        assert _anthropic_api_key_var() not in names
        assert _openrouter_api_key_var() not in names


class TestEnvReport:
    def test_shows_sources_not_secrets(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        key_var = _anthropic_api_key_var()
        print_runtime_env_report(
            quiet=False,
            credential_sources={key_var: "env: shell"},
            port=8834,
            endpoint="anthropic",
            upstream_url="https://api.anthropic.com",
            include_agent_recipe=False,
        )
        err = capsys.readouterr().err
        assert f"{key_var}: env: shell" in err
        assert "Manual proxy recipe" in err
        assert "Manual agent recipe" not in err

    def test_launch_includes_agent_recipe(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        print_runtime_env_report(
            quiet=False,
            credential_sources={},
            port=8834,
            endpoint="anthropic",
            upstream_url=None,
            include_agent_recipe=True,
            agent="claude",
        )
        err = capsys.readouterr().err
        assert "Manual agent recipe" in err
        assert "ANTHROPIC_BASE_URL" in err

    def test_quiet_suppresses_output(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        print_runtime_env_report(
            quiet=True,
            credential_sources={_openai_api_key_var(): "env: shell"},
            port=8834,
            endpoint="openai",
            upstream_url=None,
            include_agent_recipe=True,
            agent="codex",
            config={},
        )
        assert capsys.readouterr().err == ""


class TestPrepareRuntime:
    def test_launch_confirm_writes_config(
        self,
        isolated_config_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_config = isolated_config_paths["user_config"]
        monkeypatch.setenv(_anthropic_api_key_var(), "test-key")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(
            "cyt.launch.upstream.prompt_confirm_default_upstream",
            lambda agent: ("https://api.anthropic.com", "anthropic", "anthropic"),
        )

        runtime = prepare_runtime(
            agent="claude",
            config_path=user_config,
            port=None,
            upstream_url=None,
            upstream_kind=None,
            upstream_name=None,
        )

        assert user_config.exists()
        assert runtime.upstream_endpoint == "anthropic"

    def test_proxy_uses_existing_upstream_without_prompt(
        self,
        isolated_config_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_config = isolated_config_paths["user_config"]
        _write_upstream_config(
            user_config,
            name="openai",
            kind="openai",
            url="https://api.openai.com",
        )
        prompt = MagicMock(side_effect=AssertionError("should not prompt"))
        monkeypatch.setattr("cyt.launch.upstream.prompt_upstream_setup", prompt)

        prepare_runtime(
            agent=None,
            config_path=user_config,
            port=None,
            upstream_url=None,
            upstream_kind=None,
            upstream_name=None,
        )
        prompt.assert_not_called()

    def test_proxy_uses_bundled_defaults_without_prompt(
        self,
        isolated_config_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_config = isolated_config_paths["user_config"]
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        prompt = MagicMock(side_effect=AssertionError("should not prompt"))
        monkeypatch.setattr("cyt.launch.upstream.prompt_upstream_setup", prompt)

        prepare_runtime(
            agent=None,
            config_path=user_config,
            port=None,
            upstream_url=None,
            upstream_kind=None,
            upstream_name=None,
            resolve_credentials=False,
        )
        prompt.assert_not_called()

    def test_proxy_upstream_kind_inferred(
        self,
        isolated_config_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_config = isolated_config_paths["user_config"]
        monkeypatch.setenv(_anthropic_api_key_var(), "test-key")

        runtime = prepare_runtime(
            agent=None,
            config_path=user_config,
            port=None,
            upstream_url="https://api.openai.com",
            upstream_kind=None,
            upstream_name=None,
        )
        assert runtime.upstream_endpoint == "openai"


class TestCredentials:
    def test_env_skips_prompt(
        self,
        isolated_config_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_config = isolated_config_paths["user_config"]
        _write_upstream_config(
            user_config,
            name="anthropic",
            kind="anthropic",
            url="https://api.anthropic.com",
        )
        user_config.write_text(
            user_config.read_text(encoding="utf-8")
            + """
pruning:
  pipeline: [llm]
  llm:
    model:
      remote:
        model_nick: mercury-2
""",
            encoding="utf-8",
        )
        monkeypatch.setenv(_openrouter_api_key_var(), "from-shell")
        config = configs.load_config(user_config)
        sources: dict[str, str] = {}
        ensure_runtime_credentials(
            config,
            agent="claude",
            credential_sources=sources,
            endpoint="anthropic",
        )
        assert sources[_openrouter_api_key_var()] == "env: shell"
        assert _anthropic_api_key_var() not in sources


class TestCodexProvider:
    def test_restore_removes_managed_block(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        codex_path = tmp_path / "config.toml"
        monkeypatch.setattr("cyt.launch.codex.CODEX_CONFIG_PATH", codex_path)
        env_key = _codex_openai_api_key_var()
        configure_provider(port=8834, endpoint="openai", env_key=env_key)
        restore_provider(env_key=env_key)
        text = codex_path.read_text(encoding="utf-8")
        assert MANAGED_START not in text
        assert MANAGED_END not in text

    def test_rejects_managed_override_args(self) -> None:
        with pytest.raises(SystemExit, match="Cannot override cyt-managed"):
            validate_agent_args(["-c", 'model_provider="other"'])

    def test_refreshes_provider_when_port_changes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        codex_path = tmp_path / "config.toml"
        monkeypatch.setattr("cyt.launch.codex.CODEX_CONFIG_PATH", codex_path)
        env_key = _codex_openai_api_key_var()
        configure_provider(port=8834, endpoint="openai", env_key=env_key)
        ensure_provider_configured(port=9999, endpoint="openai", env_key=env_key)
        assert managed_provider_base_url() == "http://127.0.0.1:9999/openai/v1"


class TestEnsureProxy:
    def test_spawns_when_health_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        health_calls = {"count": 0}

        def fake_health(port: int) -> bool:
            health_calls["count"] += 1
            return health_calls["count"] > 1

        process = MagicMock()
        process.poll.return_value = None
        monkeypatch.setattr("cyt.launch.proxy_guard._health_ok", fake_health)
        monkeypatch.setattr("cyt.launch.proxy_guard._spawn_proxy", lambda **kwargs: process)

        guard = ensure_proxy(port=8834)
        assert guard.started_by_launch is True
        assert guard.process is process


class TestLaunchRun:
    def test_launch_claude_exec(
        self,
        isolated_config_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_config = isolated_config_paths["user_config"]
        _write_upstream_config(
            user_config,
            name="anthropic",
            kind="anthropic",
            url="https://api.anthropic.com",
        )
        monkeypatch.setenv(_anthropic_api_key_var(), "test-key")
        monkeypatch.setattr("cyt.launch.proxy_guard.ensure_proxy", lambda **kwargs: MagicMock())
        monkeypatch.setattr("cyt.launch.cli.run_claude", lambda **kwargs: 0)

        args = MagicMock(
            config=user_config,
            port=None,
            upstream=None,
            upstream_kind=None,
            upstream_name=None,
            endpoint=None,
            configure=False,
            restore=False,
            quiet=True,
            remainder=["--", "claude", "-p", "hi"],
        )

        with pytest.raises(SystemExit) as exc:
            run_launch(args)
        assert exc.value.code == 0

    def test_restore_without_credentials(
        self,
        isolated_config_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        codex_path = tmp_path / "config.toml"
        monkeypatch.setattr("cyt.launch.codex.CODEX_CONFIG_PATH", codex_path)
        configure_provider(
            port=8834,
            endpoint="openai",
            env_key=_codex_openai_api_key_var(),
        )

        args = MagicMock(
            config=isolated_config_paths["user_config"],
            port=None,
            upstream=None,
            upstream_kind=None,
            upstream_name=None,
            endpoint=None,
            configure=False,
            restore=True,
            quiet=True,
            remainder=["--", "codex"],
        )

        run_launch(args)
        text = codex_path.read_text(encoding="utf-8")
        assert MANAGED_START not in text

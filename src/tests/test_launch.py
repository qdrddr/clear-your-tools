"""Tests for ``cyt launch`` and shared runtime bootstrap."""

from __future__ import annotations

import subprocess
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
from cyt.launch.proxy_guard import (
    _spawn_proxy,
    ensure_proxy,
    find_available_port,
    require_healthy_proxy,
    resolve_launch_port,
    resolve_launch_proxy_port,
)
from cyt.launch.secrets import CYT_SKIP_KEYRING_ENV, ensure_runtime_credentials
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
                                {"endpoint": name, "kind": kind, "url": url},
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
            {"endpoint": "openai", "kind": "openai", "url": "https://api.openai.com"},
            {"endpoint": "anthropic", "kind": "anthropic", "url": "https://api.anthropic.com"},
            {
                "endpoint": "openrouter",
                "kind": "anthropic",
                "url": "https://openrouter.ai/api",
            },
        ]
        names = [entry["endpoint"] for entry in filter_upstreams_by_agent(upstreams, "claude")]
        assert names == ["anthropic", "openrouter"]

    def test_codex_keeps_openai_only(self) -> None:
        upstreams = [
            {"endpoint": "openai", "kind": "openai", "url": "https://api.openai.com"},
            {"endpoint": "anthropic", "kind": "anthropic", "url": "https://api.anthropic.com"},
        ]
        names = [entry["endpoint"] for entry in filter_upstreams_by_agent(upstreams, "codex")]
        assert names == ["openai"]


class TestSelectUpstreamEndpoint:
    def test_auto_selects_single_match(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        upstreams = [
            {"endpoint": "openai", "kind": "openai", "url": "https://api.openai.com"},
            {"endpoint": "anthropic", "kind": "anthropic", "url": "https://api.anthropic.com"},
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
            {"endpoint": "anthropic", "kind": "anthropic", "url": "https://api.anthropic.com"},
            {
                "endpoint": "openrouter",
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
                                "endpoint": "openai",
                                "kind": "openai",
                                "url": "https://api.openai.com",
                            },
                            {
                                "endpoint": "anthropic",
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
                                "endpoint": "anthropic",
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
                "tools": {
                    "sequence": ["rerank"],
                    "pipelines": {"rerank": {"model_nick": "rerank-qwen3-8b"}},
                },
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
                                "endpoint": "anthropic",
                                "kind": "anthropic",
                                "url": "https://api.anthropic.com",
                            },
                            {
                                "endpoint": "openrouter",
                                "kind": "anthropic",
                                "url": "https://openrouter.ai/api",
                            },
                        ],
                    },
                },
            },
            "pruning": {"tools": {"sequence": ["bm25"]}},
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
        assert "Proxy:" in err
        assert "  port: 8834" in err
        assert "  endpoint: http://localhost:8834/anthropic" in err
        assert "cyt proxy --port 8834 --upstream https://api.anthropic.com" in err
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
        assert "  agent: claude" in err
        assert "ANTHROPIC_BASE_URL" in err
        assert "cyt proxy --port 8834" in err

    def test_shows_detected_free_port(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        print_runtime_env_report(
            quiet=False,
            credential_sources={},
            port=8836,
            endpoint="openai",
            upstream_url=None,
            include_agent_recipe=True,
            agent="codex",
            config={},
        )
        err = capsys.readouterr().err
        assert "  port: 8836" in err
        assert "http://localhost:8836/openai" in err
        assert "cyt proxy --port 8836" in err
        assert 'model_providers.cyt.base_url="http://127.0.0.1:8836/openai/v1"' in err

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
  tools:
    sequence: [llm]
    pipelines:
      llm:
        model_nick: mercury-2
""",
            encoding="utf-8",
        )
        monkeypatch.setenv(_openrouter_api_key_var(), "from-shell")
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
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
    def test_restore_is_noop_and_preserves_managed_block(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        codex_path = tmp_path / "config.toml"
        monkeypatch.setattr("cyt.launch.codex.CODEX_CONFIG_PATH", codex_path)
        env_key = _codex_openai_api_key_var()
        configure_provider(port=8834, endpoint="openai", env_key=env_key)
        before = codex_path.read_text(encoding="utf-8")
        delete_mock = MagicMock(
            side_effect=AssertionError("must not delete keyring secrets"),
        )
        monkeypatch.setattr("keyring.delete_password", delete_mock)
        restore_provider(env_key=env_key)
        after = codex_path.read_text(encoding="utf-8")
        assert MANAGED_START in after
        assert MANAGED_END in after
        assert after == before
        delete_mock.assert_not_called()

    def test_configure_is_noop_when_block_unchanged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        codex_path = tmp_path / "config.toml"
        monkeypatch.setattr("cyt.launch.codex.CODEX_CONFIG_PATH", codex_path)
        env_key = _codex_openai_api_key_var()
        configure_provider(port=8834, endpoint="openai", env_key=env_key)
        before = codex_path.read_text(encoding="utf-8")
        ensure_provider_configured(port=8834, endpoint="openai", env_key=env_key)
        assert codex_path.read_text(encoding="utf-8") == before

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

    def test_removes_external_model_provider_when_configuring(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import tomllib

        codex_path = tmp_path / "config.toml"
        monkeypatch.setattr("cyt.launch.codex.CODEX_CONFIG_PATH", codex_path)
        codex_path.write_text(
            'model_provider = "custom_openai"\n\n[model_providers.custom_openai]\n'
            'base_url = "http://127.0.0.1:8834/openai/v1"\n',
            encoding="utf-8",
        )
        env_key = _codex_openai_api_key_var()
        configure_provider(port=8834, endpoint="openai", env_key=env_key)
        cfg = tomllib.loads(codex_path.read_text(encoding="utf-8"))
        assert cfg["model_provider"] == "cyt"
        assert "custom_openai" in cfg["model_providers"]


def _anthropic_health_payload() -> dict[str, object]:
    return {
        "name": "cyt",
        "status": "ok",
        "endpoints": ["anthropic"],
        "debug": False,
        "debug_dry_run": False,
    }


class TestFindAvailablePort:
    def test_returns_start_when_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: False,
        )
        assert find_available_port(8834) == 8834

    def test_bumps_until_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        in_use = {8834, 8835}

        def fake_in_use(port: int) -> bool:
            return port in in_use

        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            fake_in_use,
        )
        assert find_available_port(8834) == 8836

    def test_raises_when_exhausted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: True,
        )
        with pytest.raises(SystemExit, match="No free port found"):
            find_available_port(8834, max_attempts=3)


class TestResolveLaunchProxyPort:
    def test_reuses_healthy_cyt_on_base_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: port == 8834,
        )
        monkeypatch.setattr(
            "cyt.launch.proxy_guard._proxy_health",
            lambda port: _anthropic_health_payload() if port == 8834 else None,
        )

        port, action = resolve_launch_proxy_port(
            base_port=8834,
            required_endpoint="anthropic",
        )
        assert port == 8834
        assert action == "reuse"

    def test_spawns_on_base_plus_one_when_base_free(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: False,
        )

        port, action = resolve_launch_proxy_port(
            base_port=8834,
            required_endpoint="anthropic",
        )
        assert port == 8835
        assert action == "spawn"

    def test_skips_base_when_endpoint_mismatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: port == 8834,
        )
        monkeypatch.setattr(
            "cyt.launch.proxy_guard._proxy_health",
            lambda port: (
                {
                    "name": "cyt",
                    "status": "ok",
                    "endpoints": ["openai"],
                    "debug": False,
                    "debug_dry_run": False,
                }
                if port == 8834
                else None
            ),
        )

        port, action = resolve_launch_proxy_port(
            base_port=8834,
            required_endpoint="anthropic",
        )
        assert port == 8835
        assert action == "spawn"

    def test_reuses_higher_port_when_base_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: port in {8834, 8835},
        )
        monkeypatch.setattr(
            "cyt.launch.proxy_guard._proxy_health",
            lambda port: _anthropic_health_payload() if port == 8835 else None,
        )

        port, action = resolve_launch_proxy_port(
            base_port=8834,
            required_endpoint="anthropic",
        )
        assert port == 8835
        assert action == "reuse"

    def test_skips_reuse_when_debug_flags_mismatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: port == 8834,
        )
        monkeypatch.setattr(
            "cyt.launch.proxy_guard._proxy_health",
            lambda port: _anthropic_health_payload() if port == 8834 else None,
        )

        port, action = resolve_launch_proxy_port(
            base_port=8834,
            required_endpoint="anthropic",
            debug=True,
        )
        assert port == 8835
        assert action == "spawn"

    def test_resolve_launch_port_announces_bump(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.resolve_launch_proxy_port",
            lambda **kwargs: (8837, "spawn"),
        )
        assert (
            resolve_launch_port(
                8834,
                required_endpoint="anthropic",
                quiet=False,
            )
            == 8837
        )
        err = capsys.readouterr().err
        assert "Port 8835 is in use; launching on 8837." in err

    def test_resolve_launch_port_quiet_skips_bump_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.resolve_launch_proxy_port",
            lambda **kwargs: (8837, "spawn"),
        )
        assert (
            resolve_launch_port(
                8834,
                required_endpoint="anthropic",
                quiet=True,
            )
            == 8837
        )
        assert capsys.readouterr().err == ""

    def test_resolve_launch_port_silent_when_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.resolve_launch_proxy_port",
            lambda **kwargs: (8835, "spawn"),
        )
        assert resolve_launch_port(8834, required_endpoint="anthropic") == 8835
        assert capsys.readouterr().err == ""

    def test_resolve_launch_port_defaults_above_proxy_port(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: False,
        )
        assert resolve_launch_port(8834, required_endpoint="anthropic") == 8835


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
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: False,
        )
        monkeypatch.setattr("cyt.launch.proxy_guard._proxy_health", lambda port: None)
        monkeypatch.setattr("cyt.launch.proxy_guard._health_ok", fake_health)
        monkeypatch.setattr("cyt.launch.proxy_guard._spawn_proxy", lambda **kwargs: process)
        monkeypatch.setattr("cyt.launch.proxy_guard.time.sleep", lambda _: None)

        guard = ensure_proxy(base_port=8834, required_endpoint="anthropic")
        assert guard.started_by_launch is True
        assert guard.process is process
        assert guard.port == 8835

    def test_reuses_existing_proxy_on_base_port(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: port == 8834,
        )
        monkeypatch.setattr(
            "cyt.launch.proxy_guard._proxy_health",
            lambda port: _anthropic_health_payload() if port == 8834 else None,
        )

        guard = ensure_proxy(base_port=8834, required_endpoint="anthropic")
        assert guard.started_by_launch is False
        assert guard.process is None
        assert guard.port == 8834

    def test_require_healthy_proxy_rejects_missing_debug(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard._proxy_health",
            lambda port: {"name": "cyt", "status": "ok"},
        )
        with pytest.raises(SystemExit, match="without --debug"):
            require_healthy_proxy(port=8834, debug=True)

    def test_spawn_proxy_passes_debug_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}
        spawn_env: dict[str, str] | None = None

        def fake_popen(cmd: list[str], **kwargs: object) -> MagicMock:
            nonlocal spawn_env
            captured["cmd"] = cmd
            captured["stderr"] = kwargs.get("stderr")
            env = kwargs.get("env")
            if isinstance(env, dict):
                spawn_env = {str(key): str(value) for key, value in env.items()}
            return MagicMock()

        monkeypatch.setattr("cyt.launch.proxy_guard.subprocess.Popen", fake_popen)

        _spawn_proxy(
            port=8834,
            config_path=None,
            quiet=True,
            agent="codex",
            debug=True,
            debug_dry_run=True,
            debug_strict=True,
        )

        cmd = captured["cmd"]
        assert isinstance(cmd, list)
        assert "--launch-agent" in cmd
        assert "codex" in cmd
        assert "--debug" in cmd
        assert "--debug-dry-run" in cmd
        assert "--debug-strict" in cmd
        assert "--quiet" in cmd
        assert "--no-resolve-credentials" in cmd
        assert captured["stderr"] is subprocess.DEVNULL
        assert spawn_env is not None
        assert spawn_env[CYT_SKIP_KEYRING_ENV] == "1"

    def test_ensure_proxy_exhausts_when_all_ports_occupied(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: True,
        )
        monkeypatch.setattr(
            "cyt.launch.proxy_guard._proxy_health",
            lambda port: None,
        )
        with pytest.raises(SystemExit, match="No free port found"):
            ensure_proxy(
                base_port=8834,
                required_endpoint="anthropic",
                debug=True,
                max_attempts=3,
            )

    def test_ensure_proxy_bumps_port_when_start_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        unavailable = {8835}

        def fake_in_use(port: int) -> bool:
            return port in unavailable

        process = MagicMock()
        process.poll.return_value = None

        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            fake_in_use,
        )
        monkeypatch.setattr("cyt.launch.proxy_guard._health_ok", lambda port: port == 8836)
        monkeypatch.setattr("cyt.launch.proxy_guard._spawn_proxy", lambda **kwargs: process)
        monkeypatch.setattr("cyt.launch.proxy_guard.time.sleep", lambda _: None)

        guard = ensure_proxy(base_port=8834, required_endpoint="anthropic")
        assert guard.port == 8836

    def test_ensure_proxy_bumps_after_bind_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spawned_ports: list[int] = []

        def fake_spawn(**kwargs: object) -> MagicMock:
            port = kwargs.get("port")
            assert isinstance(port, int)
            spawned_ports.append(port)
            process = MagicMock()
            process.poll.return_value = 1 if port == 8835 else None
            return process

        monkeypatch.setattr(
            "cyt.launch.proxy_guard.is_port_in_use",
            lambda port: False,
        )
        monkeypatch.setattr("cyt.launch.proxy_guard._spawn_proxy", fake_spawn)
        monkeypatch.setattr("cyt.launch.proxy_guard._health_ok", lambda port: port == 8836)
        monkeypatch.setattr("cyt.launch.proxy_guard.time.sleep", lambda _: None)

        guard = ensure_proxy(base_port=8834, required_endpoint="anthropic")
        assert spawned_ports == [8835, 8836]
        assert guard.port == 8836


class TestLaunchRun:
    def test_launch_is_silent_without_quiet_flag(
        self,
        isolated_config_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        user_config = isolated_config_paths["user_config"]
        _write_upstream_config(
            user_config,
            name="anthropic",
            kind="anthropic",
            url="https://api.anthropic.com",
        )
        monkeypatch.setenv(_anthropic_api_key_var(), "test-key")
        monkeypatch.setattr(
            "cyt.launch.cli.ensure_proxy",
            lambda **kwargs: MagicMock(port=8835),
        )
        monkeypatch.setattr("cyt.launch.cli.require_healthy_proxy", lambda **kwargs: None)
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
            quiet=False,
            debug=False,
            debug_dry_run=False,
            debug_strict=False,
            remainder=["--", "claude", "-p", "hi"],
        )

        with pytest.raises(SystemExit):
            run_launch(args)

        err = capsys.readouterr().err
        assert "Proxy:" not in err
        assert "Manual agent recipe" not in err

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
        monkeypatch.setattr(
            "cyt.launch.cli.ensure_proxy",
            lambda **kwargs: MagicMock(port=8835),
        )
        monkeypatch.setattr("cyt.launch.cli.require_healthy_proxy", lambda **kwargs: None)
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

    def test_launch_uses_next_free_port(
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
        captured_ports: list[int] = []

        def fake_ensure_proxy(**kwargs: object) -> MagicMock:
            base_port = kwargs.get("base_port")
            if isinstance(base_port, int):
                captured_ports.append(base_port)
            return MagicMock(port=8835)

        monkeypatch.setattr("cyt.launch.cli.ensure_proxy", fake_ensure_proxy)
        monkeypatch.setattr("cyt.launch.cli.require_healthy_proxy", lambda **kwargs: None)
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
        assert captured_ports == [8834]

    def test_launch_uses_port_from_proxy_guard(
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
        captured_start_ports: list[int] = []
        captured_runtime_ports: list[int] = []

        def fake_ensure_proxy(**kwargs: object) -> MagicMock:
            base_port = kwargs.get("base_port")
            if isinstance(base_port, int):
                captured_start_ports.append(base_port)
            return MagicMock(port=8836)

        def fake_run_claude(**kwargs: object) -> int:
            port = kwargs.get("port")
            if isinstance(port, int):
                captured_runtime_ports.append(port)
            return 0

        monkeypatch.setattr("cyt.launch.cli.ensure_proxy", fake_ensure_proxy)
        monkeypatch.setattr("cyt.launch.cli.require_healthy_proxy", lambda **kwargs: None)
        monkeypatch.setattr("cyt.launch.cli.run_claude", fake_run_claude)

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
        assert captured_start_ports == [8834]
        assert captured_runtime_ports == [8836]

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
        assert MANAGED_START in text

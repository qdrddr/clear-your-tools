"""Tests for non-canonical upstream credential resolution."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from cyt import config as configs
from cyt.agents.claude.launch import build_claude_env
from cyt.config import load_config
from cyt.launch.agent_credentials import AgentAuthBinding, ensure_agent_upstream_auth
from cyt.launch.secrets import clear_keyring_cache
from cyt.launch.upstream_credentials import (
    describe_upstream_key_var_resolution,
    ensure_upstream_credentials,
    format_upstream_key_var_resolution_line,
    is_canonical_upstream,
    lookup_upstream_key_var,
    upstream_for_endpoint,
)
from tests.support.credential_helpers import (
    DEFAULT_CREDENTIAL_ENV_VARS,
    install_test_pre_dotenv,
    isolate_credential_env_paths,
)


def _credential_env_vars() -> tuple[str, ...]:
    return DEFAULT_CREDENTIAL_ENV_VARS


@pytest.fixture(autouse=True)
def _isolate_credential_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Generator[None]:
    """Keep real shell, .env, and keyring credentials out of resolution tests."""
    clear_keyring_cache()
    for name in _credential_env_vars():
        monkeypatch.delenv(name, raising=False)
    isolate_credential_env_paths(monkeypatch, tmp_path)
    install_test_pre_dotenv(monkeypatch)
    monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
    yield
    clear_keyring_cache()


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


def _openrouter_upstream(*, linked: bool = True) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "endpoint": "openrouter",
        "kind": "anthropic",
        "url": "https://openrouter.ai/api",
    }
    if linked:
        entry["provider_nick"] = "openrouter-ai"
    return entry


def _anthropic_upstream() -> dict[str, Any]:
    return {
        "endpoint": "anthropic",
        "kind": "anthropic",
        "url": "https://api.anthropic.com",
    }


class TestUpstreamClassification:
    def test_openrouter_is_non_canonical(self) -> None:
        assert not is_canonical_upstream(_openrouter_upstream())

    def test_anthropic_is_canonical(self) -> None:
        assert is_canonical_upstream(_anthropic_upstream())


class TestLookupUpstreamKeyVar:
    def test_openrouter_requires_explicit_provider_link(self, isolated_config_paths: dict) -> None:
        config = load_config(isolated_config_paths["user_config"])
        key_var = lookup_upstream_key_var(config, _openrouter_upstream(linked=False))
        assert key_var is None

    def test_openrouter_uses_registry_when_linked(self, isolated_config_paths: dict) -> None:
        config = load_config(isolated_config_paths["user_config"])
        key_var = lookup_upstream_key_var(config, _openrouter_upstream())
        assert key_var == "OPENROUTER_" + "API_KEY"

    def test_anthropic_uses_registry(self, isolated_config_paths: dict) -> None:
        config = load_config(isolated_config_paths["user_config"])
        key_var = lookup_upstream_key_var(config, _anthropic_upstream())
        assert key_var == "ANTHROPIC_" + "API_KEY"


class TestDescribeUpstreamKeyVarResolution:
    def test_canonical_openai_infers_provider_nick_from_url(
        self,
        isolated_config_paths: dict,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        upstream = {
            "endpoint": "openai",
            "kind": "openai",
            "url": "https://api.openai.com",
        }
        resolution = describe_upstream_key_var_resolution(config, upstream, agent="codex")

        assert resolution is not None
        assert resolution.key_var_name == "OPENAI_" + "API_KEY"
        assert resolution.provider_nick == "openai"
        assert (
            "inferred via canonical upstream https://api.openai.com"
            in resolution.provider_nick_source
        )
        assert "models.providers.openai" in resolution.provider_nick_source
        assert "matches codex agent default kind: openai" in resolution.provider_nick_source
        formatted = format_upstream_key_var_resolution_line(resolution)
        assert "Upstream API-key env var: OPENAI_" + "API_KEY" in formatted
        assert "provider_nick: openai" in formatted

    def test_linked_openrouter_uses_upstream_provider_nick(
        self,
        isolated_config_paths: dict,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        resolution = describe_upstream_key_var_resolution(config, _openrouter_upstream())

        assert resolution is not None
        assert resolution.key_var_name == "OPENROUTER_" + "API_KEY"
        assert resolution.provider_nick == "openrouter-ai"
        assert resolution.provider_nick_source == "from config.yaml upstream provider_nick"

    def test_unlinked_openrouter_is_unresolved(self, isolated_config_paths: dict) -> None:
        config = load_config(isolated_config_paths["user_config"])
        resolution = describe_upstream_key_var_resolution(
            config,
            _openrouter_upstream(linked=False),
        )

        assert resolution is not None
        assert resolution.key_var_name is None
        assert resolution.provider_nick is None
        assert "no provider_nick on upstream entry" in resolution.provider_nick_source


class TestBuildClaudeEnv:
    def _bind_openrouter(
        self,
        *,
        config: dict[str, Any],
        config_path: Path,
        sources: dict[str, str],
    ) -> tuple[dict[str, Any], AgentAuthBinding | None]:
        return ensure_agent_upstream_auth(
            agent="claude",
            config=config,
            config_path=config_path,
            endpoint="openrouter",
            credential_sources=sources,
            allow_prompt=False,
        )

    def test_preserves_anthropic_auth_token_when_openrouter_key_missing(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [_openrouter_upstream()]
        monkeypatch.delenv("OPENROUTER_" + "API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "user-token")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")

        env, _ = build_claude_env(
            config=config,
            port=8835,
            endpoint="openrouter",
            auth_binding=None,
        )
        assert env["ANTHROPIC_AUTH_TOKEN"] == "user-token"
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:8835/openrouter"

    def test_prefers_shell_auth_token_over_openrouter_key(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [_openrouter_upstream()]
        monkeypatch.setenv("OPENROUTER_" + "API_KEY", "or-token")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "user-token")
        sources: dict[str, str] = {}
        _, binding = self._bind_openrouter(
            config=config,
            config_path=isolated_config_paths["user_config"],
            sources=sources,
        )

        env, _ = build_claude_env(
            config=config,
            port=8835,
            endpoint="openrouter",
            auth_binding=binding,
        )
        assert env["ANTHROPIC_AUTH_TOKEN"] == "user-token"

    def test_uses_openrouter_key_when_auth_token_missing(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [_openrouter_upstream()]
        monkeypatch.setenv("OPENROUTER_" + "API_KEY", "or-token")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        sources: dict[str, str] = {}
        _, binding = self._bind_openrouter(
            config=config,
            config_path=isolated_config_paths["user_config"],
            sources=sources,
        )

        env, reportable = build_claude_env(
            config=config,
            port=8835,
            endpoint="openrouter",
            auth_binding=binding,
        )
        assert env["ANTHROPIC_AUTH_TOKEN"] == "or-token"
        assert "ANTHROPIC_AUTH_TOKEN" in reportable

    def test_clears_anthropic_api_key_for_openrouter(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [_openrouter_upstream()]
        monkeypatch.setenv("OPENROUTER_" + "API_KEY", "or-token")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "oauth-managed-key")
        sources: dict[str, str] = {}
        _, binding = self._bind_openrouter(
            config=config,
            config_path=isolated_config_paths["user_config"],
            sources=sources,
        )

        env, _ = build_claude_env(
            config=config,
            port=8835,
            endpoint="openrouter",
            auth_binding=binding,
        )
        assert env["ANTHROPIC_AUTH_TOKEN"] == "or-token"
        assert env["ANTHROPIC_API_KEY"] == ""

    def test_strips_oauth_env_for_openrouter(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [_openrouter_upstream()]
        monkeypatch.setenv("OPENROUTER_" + "API_KEY", "or-token")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "oauth-refresh")
        sources: dict[str, str] = {}
        _, binding = self._bind_openrouter(
            config=config,
            config_path=isolated_config_paths["user_config"],
            sources=sources,
        )

        env, _ = build_claude_env(
            config=config,
            port=8835,
            endpoint="openrouter",
            auth_binding=binding,
        )
        assert env["ANTHROPIC_AUTH_TOKEN"] == "or-token"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "CLAUDE_CODE_OAUTH_REFRESH_TOKEN" not in env


class TestEnsureUpstreamCredentials:
    def test_resolves_existing_env_without_prompt(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        monkeypatch.setenv("OPENROUTER_" + "API_KEY", "from-shell")
        sources: dict[str, str] = {}

        updated = ensure_upstream_credentials(
            config=config,
            config_path=isolated_config_paths["user_config"],
            entry=_openrouter_upstream(),
            credential_sources=sources,
            allow_prompt=False,
        )

        assert updated is not None
        assert os.environ.get("OPENROUTER_" + "API_KEY") == "from-shell"
        assert "OPENROUTER_" + "API_KEY" in sources

    def test_upstream_key_resolves_independently_of_auth_token(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        monkeypatch.delenv("OPENROUTER_" + "API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "from-shell-auth")
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
        sources: dict[str, str] = {}

        with pytest.raises(SystemExit):
            ensure_upstream_credentials(
                config=config,
                config_path=isolated_config_paths["user_config"],
                entry=_openrouter_upstream(),
                credential_sources=sources,
                allow_prompt=False,
            )

    def test_upstream_for_endpoint(self, isolated_config_paths: dict) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [_openrouter_upstream(), _anthropic_upstream()]
        entry = upstream_for_endpoint(config, "openrouter")
        assert entry is not None
        assert entry["url"] == "https://openrouter.ai/api"


class TestClaudeLaunchRuntimeCredentials:
    def test_prepare_runtime_requires_openrouter_key(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from cyt.proxy.bootstrap import prepare_runtime

        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [_openrouter_upstream()]
        config["network"]["proxy"]["reverse"]["endpoints"] = ["openrouter"]
        config.setdefault("pruning", {}).setdefault("tools", {})["sequence"] = ["llm"]
        config["pruning"]["tools"].setdefault("pipelines", {})["llm"] = {
            "model_nick": "mercury-2",
        }
        isolated_config_paths["user_config"].parent.mkdir(parents=True, exist_ok=True)
        import yaml

        isolated_config_paths["user_config"].write_text(yaml.dump(config), encoding="utf-8")

        monkeypatch.delenv("OPENROUTER_" + "API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "from-shell-auth")
        monkeypatch.setenv("DEEPINFRA_" + "API_KEY", "deepinfra-token")
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)

        with pytest.raises(SystemExit):
            prepare_runtime(
                agent="claude",
                config_path=isolated_config_paths["user_config"],
                port=None,
                upstream_url=None,
                upstream_kind=None,
                upstream_name=None,
            )

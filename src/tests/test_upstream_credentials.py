"""Tests for non-canonical upstream credential resolution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from cyt import config as configs
from cyt.config import load_config
from cyt.launch.claude import build_claude_env
from cyt.launch.upstream_credentials import (
    ensure_upstream_credentials,
    is_canonical_upstream,
    lookup_upstream_key_var,
    upstream_for_endpoint,
)


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


def _openrouter_upstream() -> dict[str, Any]:
    return {
        "endpoint": "openrouter",
        "kind": "anthropic",
        "url": "https://openrouter.ai/api",
    }


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
    def test_openrouter_uses_registry(self, isolated_config_paths: dict) -> None:
        config = load_config(isolated_config_paths["user_config"])
        key_var = lookup_upstream_key_var(config, _openrouter_upstream())
        assert key_var == "OPENROUTER_" + "API_KEY"

    def test_anthropic_uses_registry(self, isolated_config_paths: dict) -> None:
        config = load_config(isolated_config_paths["user_config"])
        key_var = lookup_upstream_key_var(config, _anthropic_upstream())
        assert key_var == "ANTHROPIC_" + "API_KEY"


class TestBuildClaudeEnv:
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

        env, _ = build_claude_env(config=config, port=8835, endpoint="openrouter")
        assert env["ANTHROPIC_AUTH_TOKEN"] == "user-token"
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:8835/openrouter"

    def test_uses_openrouter_key_when_set(
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

        env, _ = build_claude_env(config=config, port=8835, endpoint="openrouter")
        assert env["ANTHROPIC_AUTH_TOKEN"] == "or-token"


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

    def test_upstream_for_endpoint(self, isolated_config_paths: dict) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [_openrouter_upstream(), _anthropic_upstream()]
        entry = upstream_for_endpoint(config, "openrouter")
        assert entry is not None
        assert entry["url"] == "https://openrouter.ai/api"

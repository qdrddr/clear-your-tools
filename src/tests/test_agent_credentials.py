"""Tests for agent-facing non-canonical upstream credential wiring."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

import cyt.config as configs
from cyt.config import load_config
from cyt.launch.agent_credentials import ensure_agent_upstream_auth, ensure_codex_agent_auth
from cyt.launch.secrets import clear_keyring_cache
from tests.test_credential_helpers import (
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


def _openrouter_upstream() -> dict[str, Any]:
    return {
        "endpoint": "openrouter",
        "kind": "anthropic",
        "url": "https://openrouter.ai/api",
        "provider_nick": "openrouter",
    }


def _openrouter_api_key_var() -> str:
    return "OPENROUTER_" + "API_KEY"


def _openai_api_key_var() -> str:
    return "OPENAI_" + "API_KEY"


def _codex_openai_api_key_var() -> str:
    return "CODEX_OPENAI_" + "API_KEY"


def _or_token() -> str:
    return "or-" + "token"


def _openai_token() -> str:
    return "openai-" + "token"


def _auth_json_token() -> str:
    return "auth-json-" + "token"


def _openai_upstream() -> dict[str, Any]:
    return {
        "endpoint": "openai",
        "kind": "openai",
        "url": "https://api.openai.com",
    }


class TestEnsureAgentUpstreamAuth:
    def test_claude_prefers_anthropic_auth_token_env(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [_openrouter_upstream()]
        monkeypatch.setenv(_openrouter_api_key_var(), _or_token())
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "direct-token")
        sources: dict[str, str] = {}

        _, binding = ensure_agent_upstream_auth(
            agent="claude",
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openrouter",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert binding.agent_env_var == "ANTHROPIC_AUTH_TOKEN"
        assert binding.token == "direct-token"
        assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "direct-token"
        assert os.environ[_openrouter_api_key_var()] == _or_token()
        assert sources[_openrouter_api_key_var()] in {"env: process", "env: shell"}
        assert sources["ANTHROPIC_AUTH_TOKEN"] in {"env: process", "env: shell"}
        assert "via ANTHROPIC_AUTH_TOKEN" not in sources.get(_openrouter_api_key_var(), "")

    def test_claude_uses_provider_key_when_auth_token_missing(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [_openrouter_upstream()]
        monkeypatch.setenv(_openrouter_api_key_var(), _or_token())
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        sources: dict[str, str] = {}

        _, binding = ensure_agent_upstream_auth(
            agent="claude",
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openrouter",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert os.environ["ANTHROPIC_AUTH_TOKEN"] == _or_token()
        assert "via OPENROUTER_" in sources["ANTHROPIC_AUTH_TOKEN"]

    def test_codex_sets_codex_openai_api_key_from_provider_key(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [
            {
                "endpoint": "openrouter",
                "kind": "openai",
                "url": "https://openrouter.ai/api",
                "provider_nick": "openrouter",
            },
        ]
        monkeypatch.setenv(_openrouter_api_key_var(), _or_token())
        monkeypatch.delenv(_codex_openai_api_key_var(), raising=False)
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
        sources: dict[str, str] = {}

        _, binding = ensure_agent_upstream_auth(
            agent="codex",
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openrouter",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert binding.agent_env_var == _codex_openai_api_key_var()
        assert os.environ[_codex_openai_api_key_var()] == _or_token()

    def test_codex_uses_upstream_shell_over_codex_shell(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [
            {
                "endpoint": "openrouter",
                "kind": "openai",
                "url": "https://openrouter.ai/api",
                "provider_nick": "openrouter",
            },
        ]
        monkeypatch.setenv(_codex_openai_api_key_var(), "codex-direct")
        monkeypatch.setenv(_openrouter_api_key_var(), _or_token())
        sources: dict[str, str] = {}

        _, binding = ensure_agent_upstream_auth(
            agent="codex",
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openrouter",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert binding.token == _or_token()
        assert os.environ[_codex_openai_api_key_var()] == _or_token()
        assert os.environ[_openrouter_api_key_var()] == _or_token()

    def test_codex_keyring_resolves_upstream_key_from_keyring(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [
            {
                "endpoint": "openrouter",
                "kind": "openai",
                "url": "https://openrouter.ai/api",
                "provider_nick": "openrouter",
            },
        ]
        monkeypatch.delenv(_openrouter_api_key_var(), raising=False)
        monkeypatch.delenv(_codex_openai_api_key_var(), raising=False)
        monkeypatch.setattr(
            "cyt.launch.secrets._read_keyring",
            lambda name: {
                _codex_openai_api_key_var(): "codex-direct",
                _openrouter_api_key_var(): _or_token(),
            }.get(name),
        )
        sources: dict[str, str] = {}

        _, binding = ensure_codex_agent_auth(
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openrouter",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert binding.token == "codex-direct"
        assert binding.upstream_key_var == _openrouter_api_key_var()
        assert os.environ[_codex_openai_api_key_var()] == "codex-direct"
        assert os.environ[_openrouter_api_key_var()] == _or_token()
        assert sources[_openrouter_api_key_var()] == "keyring"
        assert sources[_codex_openai_api_key_var()] == "keyring"

    def test_codex_auth_json_resolves_upstream_key_from_keyring(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [
            {
                "endpoint": "openrouter",
                "kind": "openai",
                "url": "https://openrouter.ai/api",
                "provider_nick": "openrouter",
            },
        ]
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    _openai_api_key_var(): _auth_json_token(),
                },
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("cyt.launch.codex.CODEX_AUTH_PATH", auth_path)
        monkeypatch.delenv(_codex_openai_api_key_var(), raising=False)
        monkeypatch.delenv(_openrouter_api_key_var(), raising=False)
        monkeypatch.setattr(
            "cyt.launch.secrets._read_keyring",
            lambda name: _or_token() if name == _openrouter_api_key_var() else None,
        )
        sources: dict[str, str] = {}

        _, binding = ensure_codex_agent_auth(
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openrouter",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert binding.token == _auth_json_token()
        assert binding.upstream_key_var == _openrouter_api_key_var()
        assert os.environ[_openrouter_api_key_var()] == _or_token()
        assert sources[_openrouter_api_key_var()] == "keyring"
        assert sources[_codex_openai_api_key_var()] == f"via {_openai_api_key_var()}"
        assert sources[_openai_api_key_var()] == str(auth_path.resolve())

    def test_codex_canonical_openai_uses_provider_key(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        monkeypatch.delenv(_codex_openai_api_key_var(), raising=False)
        monkeypatch.delenv(_openai_api_key_var(), raising=False)
        monkeypatch.setenv(_openai_api_key_var(), _openai_token())
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "stale-codex-key")
        sources: dict[str, str] = {}

        _, binding = ensure_codex_agent_auth(
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openai",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert binding.token == _openai_token()
        assert os.environ[_codex_openai_api_key_var()] == _openai_token()
        assert sources[_codex_openai_api_key_var()] == f"via {_openai_api_key_var()}"
        assert sources[_openai_api_key_var()] == "env: shell"

    def test_codex_reresolves_codex_key_from_keyring(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        openai_key = _openai_api_key_var()
        codex_key = _codex_openai_api_key_var()
        monkeypatch.delenv(codex_key, raising=False)
        os.environ[openai_key] = "stale-runtime-value"
        monkeypatch.setattr(
            "cyt.launch.secrets._read_keyring",
            lambda name: "keyring-" + "codex" if name == codex_key else None,
        )
        sources: dict[str, str] = {openai_key: "env: shell"}

        _, binding = ensure_codex_agent_auth(
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openai",
            credential_sources=sources,
            allow_prompt=False,
            launch_before_env={},
        )

        assert binding is not None
        assert binding.token == "keyring-" + "codex"
        assert os.environ[codex_key] == "keyring-" + "codex"
        assert sources[codex_key] == "keyring"
        assert sources[openai_key] == "env: shell"

    def test_codex_uses_auth_json_before_keyring(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    _openai_api_key_var(): _auth_json_token(),
                },
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("cyt.launch.codex.CODEX_AUTH_PATH", auth_path)
        monkeypatch.delenv(_codex_openai_api_key_var(), raising=False)
        monkeypatch.delenv(_openai_api_key_var(), raising=False)
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "keyring-" + "token")
        sources: dict[str, str] = {}

        _, binding = ensure_codex_agent_auth(
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openai",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert binding.token == _auth_json_token()
        assert sources[_codex_openai_api_key_var()] == f"via {_openai_api_key_var()}"
        assert sources[_openai_api_key_var()] == str(auth_path.resolve())

    def test_codex_skips_null_auth_json_openai_key(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(
            json.dumps({"auth_mode": "chatgpt", _openai_api_key_var(): None}),
            encoding="utf-8",
        )
        monkeypatch.setattr("cyt.launch.codex.CODEX_AUTH_PATH", auth_path)
        monkeypatch.delenv(_codex_openai_api_key_var(), raising=False)
        monkeypatch.setenv(_openai_api_key_var(), _openai_token())
        sources: dict[str, str] = {}

        _, binding = ensure_codex_agent_auth(
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openai",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert binding.token == _openai_token()
        assert sources[_codex_openai_api_key_var()] == f"via {_openai_api_key_var()}"
        assert sources[_openai_api_key_var()] == "env: shell"

    def test_codex_skips_empty_auth_json_openai_key(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(
            json.dumps({"auth_mode": "chatgpt", _openai_api_key_var(): ""}),
            encoding="utf-8",
        )
        monkeypatch.setattr("cyt.launch.codex.CODEX_AUTH_PATH", auth_path)
        monkeypatch.delenv(_codex_openai_api_key_var(), raising=False)
        monkeypatch.delenv(_openai_api_key_var(), raising=False)
        monkeypatch.setattr(
            "cyt.launch.secrets._read_keyring",
            lambda name: "keyring-" + "codex" if name == _codex_openai_api_key_var() else None,
        )
        sources: dict[str, str] = {}

        _, binding = ensure_codex_agent_auth(
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openai",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert binding.token == "keyring-" + "codex"
        assert sources[_codex_openai_api_key_var()] == "keyring"

    def test_codex_prefers_shell_openai_over_auth_json(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    _openai_api_key_var(): _auth_json_token(),
                },
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("cyt.launch.codex.CODEX_AUTH_PATH", auth_path)
        monkeypatch.delenv(_codex_openai_api_key_var(), raising=False)
        monkeypatch.setenv(_openai_api_key_var(), _openai_token())
        sources: dict[str, str] = {}

        _, binding = ensure_codex_agent_auth(
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openai",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert binding is not None
        assert binding.token == _openai_token()
        assert sources[_codex_openai_api_key_var()] == f"via {_openai_api_key_var()}"
        assert sources[_openai_api_key_var()] == "env: shell"
        assert sources[_codex_openai_api_key_var()] != str(auth_path.resolve())

    def test_codex_prompts_when_no_other_credentials(
        self,
        isolated_config_paths: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = load_config(isolated_config_paths["user_config"])
        monkeypatch.delenv(_codex_openai_api_key_var(), raising=False)
        monkeypatch.delenv(_openai_api_key_var(), raising=False)
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(
            "cyt.launch.secrets.getpass.getpass",
            lambda _prompt: "prompt-" + "secret",
        )
        writes: list[tuple[str, str]] = []

        def write_keyring(key: str, value: str) -> bool:
            writes.append((key, value))
            return True

        monkeypatch.setattr("cyt.launch.secrets._write_keyring", write_keyring)
        sources: dict[str, str] = {}

        _, binding = ensure_codex_agent_auth(
            config=config,
            config_path=isolated_config_paths["user_config"],
            endpoint="openai",
            credential_sources=sources,
            allow_prompt=True,
        )

        assert binding is not None
        assert binding.token == "prompt-" + "secret"
        assert sources[_codex_openai_api_key_var()] == "keyring"
        assert writes == [(_codex_openai_api_key_var(), "prompt-" + "secret")]

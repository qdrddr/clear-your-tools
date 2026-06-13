"""Tests for launch credential resolution order and persistence."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import cyt.config as configs
from cyt.launch.secrets import (
    ensure_runtime_credentials,
    keyring_backend_available,
    resolve_credential,
)


def _codex_openai_api_key_var() -> str:
    return "CODEX_OPENAI_" + "API_KEY"


@pytest.fixture
def isolated_env_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    user_env = tmp_path / "home" / ".config" / "cyt" / ".env"
    cwd_env = work_dir / ".env"
    user_config = tmp_path / "home" / ".config" / "cyt" / "config.yaml"
    monkeypatch.setattr(configs, "CWD_ENV_PATH", cwd_env)
    monkeypatch.setattr(configs, "USER_ENV_PATH", user_env)
    monkeypatch.setattr(configs, "DEFAULT_USER_CONFIG_PATH", user_config)
    monkeypatch.chdir(work_dir)
    return {
        "work_dir": work_dir,
        "cwd_env": cwd_env,
        "user_env": user_env,
        "user_config": user_config,
    }


class TestResolveCredentialOrder:
    def test_keyring_preferred_over_env_file(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        isolated_env_paths["cwd_env"].write_text(f"{name}=from-dotenv\n", encoding="utf-8")
        monkeypatch.setattr(
            "cyt.launch.secrets._read_keyring",
            lambda _name: "from-keyring",
        )

        value, source = resolve_credential(name, before_env={})

        assert value == "from-keyring"
        assert source == "keyring"

    def test_env_file_used_when_keyring_empty(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        isolated_env_paths["user_env"].parent.mkdir(parents=True, exist_ok=True)
        isolated_env_paths["user_env"].write_text(f"{name}=from-user-env\n", encoding="utf-8")
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)

        value, source = resolve_credential(name, before_env={})

        assert value == "from-user-env"
        assert source == "env: ~/.config/cyt/.env"

    def test_shell_env_used_when_keyring_and_env_files_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)

        value, source = resolve_credential(name, before_env={name: "from-shell"})

        assert value == "from-shell"
        assert source == "env: shell"

    def test_prompt_persists_to_keyring(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("cyt.launch.secrets.getpass.getpass", lambda _prompt: "typed-secret")
        writes: list[tuple[str, str]] = []

        def write_keyring(key: str, value: str) -> bool:
            writes.append((key, value))
            return True

        monkeypatch.setattr("cyt.launch.secrets._write_keyring", write_keyring)

        value, source = resolve_credential(name, before_env={})

        assert value == "typed-secret"
        assert source == "keyring"
        assert writes == [(name, "typed-secret")]

    def test_prompt_falls_back_to_process_env_when_keyring_write_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import os

        name = _codex_openai_api_key_var()
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("cyt.launch.secrets.getpass.getpass", lambda _prompt: "session-only")
        monkeypatch.setattr("cyt.launch.secrets._write_keyring", lambda _key, _value: False)

        value, source = resolve_credential(name, before_env={})

        assert value == "session-only"
        assert source == "prompt"
        assert os.environ[name] == "session-only"


class TestCodexLaunchCredentials:
    def test_codex_resolves_from_keyring(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "codex-key")
        config = {
            "pruning": {"tools": {"sequence": ["bm25"]}},
            "launch": {"codex": {"env_key": name}},
        }
        sources: dict[str, str] = {}

        ensure_runtime_credentials(config, agent="codex", credential_sources=sources)

        assert sources[name] == "keyring"

    def test_codex_requires_configured_env_key(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        custom_name = "CUSTOM_CODEX_" + "API_KEY"
        monkeypatch.setenv(custom_name, "from-shell")
        config = {
            "pruning": {"tools": {"sequence": ["bm25"]}},
            "launch": {"codex": {"env_key": custom_name}},
        }
        sources: dict[str, str] = {}

        ensure_runtime_credentials(config, agent="codex", credential_sources=sources)

        assert sources[custom_name] == "env: shell"


class TestKeyringBackend:
    def test_unavailable_when_import_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "keyring", None)
        assert keyring_backend_available() is False

    def test_available_when_probe_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store: dict[tuple[str, str], str] = {}

        class FakeKeyring:
            @classmethod
            def set_password(cls, service: str, user: str, password: str) -> None:
                store[(service, user)] = password

            @classmethod
            def get_password(cls, service: str, user: str) -> str | None:
                return store.get((service, user))

            @classmethod
            def delete_password(cls, service: str, user: str) -> None:
                store.pop((service, user), None)

        monkeypatch.setitem(sys.modules, "keyring", FakeKeyring)
        assert keyring_backend_available() is True

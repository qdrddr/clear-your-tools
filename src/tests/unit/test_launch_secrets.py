"""Tests for launch credential resolution order and persistence."""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

import cyt.config as configs
from cyt.config import load_config
from cyt.launch.agent_credentials import ensure_agent_upstream_auth, ensure_codex_agent_auth
from cyt.launch.secrets import (
    KEYRING_BLOB_ACCOUNT,
    KEYRING_SERVICE,
    clear_keyring_cache,
    keyring_backend_available,
    preload_keyring_credentials,
    resolve_credential,
)
from tests.support.credential_helpers import (
    env_file_source_label,
    install_test_pre_dotenv,
    isolate_credential_env_paths,
)


def _codex_openai_api_key_var() -> str:
    return "CODEX_OPENAI_" + "API_KEY"


@pytest.fixture(autouse=True)
def _reset_credential_caches() -> Generator[None]:
    clear_keyring_cache()
    yield
    clear_keyring_cache()


@pytest.fixture(autouse=True)
def _isolate_credential_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_credential_env_paths(monkeypatch, tmp_path)
    install_test_pre_dotenv(monkeypatch)


@pytest.fixture
def isolated_env_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    paths = isolate_credential_env_paths(monkeypatch, tmp_path)
    user_config = tmp_path / "home" / ".config" / "cyt" / "config.yaml"
    monkeypatch.setattr(configs, "DEFAULT_USER_CONFIG_PATH", user_config)
    paths["user_config"] = user_config
    return paths


class TestResolveCredentialOrder:
    def test_shell_env_preferred_over_env_file_and_keyring(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        isolated_env_paths["cwd_env"].write_text(f"{name}=from-dotenv\n", encoding="utf-8")
        monkeypatch.setenv(name, "from-shell")
        monkeypatch.setattr(
            "cyt.launch.secrets._read_keyring",
            lambda _name: "from-keyring",
        )

        value, source = resolve_credential(name)

        assert value == "from-shell"
        assert source == "env: shell"

    def test_import_time_env_file_not_mislabeled_as_shell(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = "OPENAI_" + "API_KEY"
        isolated_env_paths["user_env"].parent.mkdir(parents=True, exist_ok=True)
        isolated_env_paths["user_env"].write_text(f"{name}=from-user-env\n", encoding="utf-8")
        monkeypatch.delenv(name, raising=False)
        # Simulate load_proxy_env() at import: value is in os.environ but not the shell.
        os.environ[name] = "from-user-env"
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "from-keyring")

        value, source = resolve_credential(name, allow_prompt=False)

        assert value == "from-user-env"
        assert source == env_file_source_label(isolated_env_paths["user_env"])

    def test_env_file_preferred_over_keyring(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        isolated_env_paths["user_env"].parent.mkdir(parents=True, exist_ok=True)
        isolated_env_paths["user_env"].write_text(f"{name}=from-user-env\n", encoding="utf-8")
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "from-keyring")

        value, source = resolve_credential(name)

        assert value == "from-user-env"
        assert source == env_file_source_label(isolated_env_paths["user_env"])

    def test_env_file_used_when_keyring_empty(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        isolated_env_paths["user_env"].parent.mkdir(parents=True, exist_ok=True)
        isolated_env_paths["user_env"].write_text(f"{name}=from-user-env\n", encoding="utf-8")
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)

        value, source = resolve_credential(name)

        assert value == "from-user-env"
        assert source == env_file_source_label(isolated_env_paths["user_env"])

    def test_keyring_used_when_shell_and_env_files_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "from-keyring")

        value, source = resolve_credential(name)

        assert value == "from-keyring"
        assert source == "keyring"

    def test_post_import_process_env_not_mislabeled_as_shell(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Values injected after import (e.g. LiteLLM ``load_dotenv``) are not shell exports."""
        name = "OPENAI_" + "API_KEY"
        monkeypatch.delenv(name, raising=False)
        os.environ[name] = "from-litellm-dotenv"
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "from-keyring")

        value, source = resolve_credential(name, allow_prompt=False)

        assert value == "from-keyring"
        assert source == "keyring"

    def test_shell_env_used_when_keyring_and_env_files_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        monkeypatch.setenv(name, "from-shell")
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)

        value, source = resolve_credential(name)

        assert value == "from-shell"
        assert source == "env: shell"

    def test_shell_export_matching_env_file_reports_file_source(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        isolated_env_paths["user_env"].parent.mkdir(parents=True, exist_ok=True)
        isolated_env_paths["user_env"].write_text(f"{name}=shared-secret\n", encoding="utf-8")
        monkeypatch.setenv(name, "shared-secret")
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)

        value, source = resolve_credential(name)

        assert value == "shared-secret"
        assert source == env_file_source_label(isolated_env_paths["user_env"])

    def test_prompt_persists_to_keyring(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("cyt.launch.secrets.getpass.getpass", lambda _prompt: "typed-secret")
        writes: list[tuple[str, str]] = []

        def write_keyring(key: str, value: str) -> bool:
            writes.append((key, value))
            return True

        monkeypatch.setattr("cyt.launch.secrets._write_keyring", write_keyring)

        value, source = resolve_credential(name)

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

        value, source = resolve_credential(name)

        assert value == "session-only"
        assert source == "prompt"
        assert os.environ[name] == "session-only"

    def test_ensure_wizard_credentials_writes_env_when_keyring_write_fails(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from cyt.launch.secrets import ensure_wizard_credentials

        name = "OPENROUTER_" + "API_KEY"
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("cyt.launch.secrets.getpass.getpass", lambda _prompt: "from-prompt")
        monkeypatch.setattr("cyt.launch.secrets._write_keyring", lambda _key, _value: False)

        sources = ensure_wizard_credentials([name])

        assert sources[name] == env_file_source_label(isolated_env_paths["user_env"])
        assert isolated_env_paths["user_env"].read_text(encoding="utf-8") == f"{name}=from-prompt\n"


class TestCodexLaunchCredentials:
    def test_keyring_value_not_mislabeled_as_shell_on_second_resolve(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "from-keyring")

        first_value, first_source = resolve_credential(name, allow_prompt=False)
        assert first_value == "from-keyring"
        assert first_source == "keyring"

        second_value, second_source = resolve_credential(name, allow_prompt=False)
        assert second_value == "from-keyring"
        assert second_source == "keyring"

    def test_codex_resolves_from_provider_key_via_launch_auth(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        config = load_config(isolated_env_paths["user_config"])
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [
            {
                "endpoint": "openai",
                "kind": "openai",
                "url": "https://api.openai.com",
            },
        ]
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("OPENAI_" + "API_KEY", "openai-" + "token")
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "stale-codex-key")
        sources: dict[str, str] = {}

        ensure_agent_upstream_auth(
            agent="codex",
            config=config,
            config_path=isolated_env_paths["user_config"],
            endpoint="openai",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert os.environ[name] == "openai-" + "token"
        assert "via OPENAI_" in sources[name]

    def test_codex_requires_configured_env_key(
        self,
        isolated_env_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        custom_name = "CUSTOM_CODEX_" + "API_KEY"
        monkeypatch.delenv("OPENAI_" + "API_KEY", raising=False)
        config = load_config(isolated_env_paths["user_config"])
        config["launch"] = {"codex": {"env_key": custom_name}}
        config.setdefault("network", {}).setdefault("proxy", {}).setdefault("reverse", {})[
            "upstreams"
        ] = [
            {
                "endpoint": "openai",
                "kind": "openai",
                "url": "https://api.openai.com",
            },
        ]
        monkeypatch.setattr(
            "cyt.launch.secrets._read_keyring",
            lambda name: "from-keyring" if name == custom_name else None,
        )
        sources: dict[str, str] = {}

        ensure_codex_agent_auth(
            config=config,
            config_path=isolated_env_paths["user_config"],
            endpoint="openai",
            credential_sources=sources,
            allow_prompt=False,
        )

        assert os.environ[custom_name] == "from-keyring"
        assert sources[custom_name] == "keyring"


class TestKeyringBackend:
    def test_unavailable_when_import_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "keyring", None)
        assert keyring_backend_available() is False

    def test_available_when_backend_is_usable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class UsableBackend:
            __module__ = "keyring.backends.macOS"

        class FakeKeyring:
            @staticmethod
            def get_keyring() -> UsableBackend:
                return UsableBackend()

        monkeypatch.setitem(sys.modules, "keyring", FakeKeyring)
        assert keyring_backend_available() is True

    def test_unavailable_when_fail_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FailBackend:
            __module__ = "keyring.backends.fail"

        class FakeKeyring:
            @staticmethod
            def get_keyring() -> FailBackend:
                return FailBackend()

        monkeypatch.setitem(sys.modules, "keyring", FakeKeyring)
        assert keyring_backend_available() is False


class TestKeyringBlob:
    @pytest.fixture
    def fake_keyring_store(self, monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
        store: dict[tuple[str, str], str] = {}

        class UsableBackend:
            __module__ = "keyring.backends.macOS"

        class FakeKeyring:
            @staticmethod
            def get_keyring() -> UsableBackend:
                return UsableBackend()

            @staticmethod
            def get_password(service: str, account: str) -> str | None:
                return store.get((service, account))

            @staticmethod
            def set_password(service: str, account: str, password: str) -> None:
                store[(service, account)] = password

        monkeypatch.setitem(sys.modules, "keyring", FakeKeyring)
        clear_keyring_cache()
        return store

    def test_blob_stores_and_reads_multiple_credentials(
        self,
        fake_keyring_store: dict[tuple[str, str], str],
    ) -> None:
        from cyt.launch.secrets import _read_keyring, _write_keyring

        assert _write_keyring("KEY_A", "secret-a")
        assert _write_keyring("KEY_B", "secret-b")
        clear_keyring_cache()

        assert _read_keyring("KEY_A") == "secret-a"
        assert _read_keyring("KEY_B") == "secret-b"
        assert (KEYRING_SERVICE, KEYRING_BLOB_ACCOUNT) in fake_keyring_store

    def test_preload_reads_blob_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from cyt.launch.secrets import _write_keyring

        store: dict[tuple[str, str], str] = {}
        calls = {"count": 0}

        class UsableBackend:
            __module__ = "keyring.backends.macOS"

        class FakeKeyring:
            @staticmethod
            def get_keyring() -> UsableBackend:
                return UsableBackend()

            @staticmethod
            def get_password(service: str, account: str) -> str | None:
                calls["count"] += 1
                return store.get((service, account))

            @staticmethod
            def set_password(service: str, account: str, password: str) -> None:
                store[(service, account)] = password

        monkeypatch.setitem(sys.modules, "keyring", FakeKeyring)
        clear_keyring_cache()

        _write_keyring("KEY_A", "secret-a")
        _write_keyring("KEY_B", "secret-b")
        clear_keyring_cache()
        calls["count"] = 0

        preload_keyring_credentials(["KEY_A", "KEY_B", "KEY_A"])

        # One blob read plus one legacy-slot lookup per unique credential name.
        assert calls["count"] == 3

    def test_migrates_legacy_per_key_entries_to_blob(
        self,
        fake_keyring_store: dict[tuple[str, str], str],
    ) -> None:
        from cyt.launch.secrets import _read_keyring

        name = "OPENROUTER_" + "API_KEY"
        fake_keyring_store[(KEYRING_SERVICE, name)] = "legacy-secret"
        clear_keyring_cache()

        assert _read_keyring(name) == "legacy-secret"
        assert (KEYRING_SERVICE, KEYRING_BLOB_ACCOUNT) in fake_keyring_store

    def test_prefers_legacy_per_key_entry_when_blob_value_is_stale(
        self,
        fake_keyring_store: dict[tuple[str, str], str],
    ) -> None:
        from cyt.launch.secrets import KEYRING_BLOB_ACCOUNT, _encode_keyring_blob, _read_keyring

        name = "OPENROUTER_" + "API_KEY"
        stale = "legacy-secret"
        current = "sk-or-v1-" + ("x" * 64)
        fake_keyring_store[(KEYRING_SERVICE, KEYRING_BLOB_ACCOUNT)] = _encode_keyring_blob(
            {name: stale},
        )
        fake_keyring_store[(KEYRING_SERVICE, name)] = current
        clear_keyring_cache()

        assert _read_keyring(name) == current
        assert fake_keyring_store[(KEYRING_SERVICE, KEYRING_BLOB_ACCOUNT)] == _encode_keyring_blob(
            {name: current},
        )

    def test_skip_keyring_uses_process_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        name = _codex_openai_api_key_var()
        monkeypatch.setenv("CYT_SKIP_KEYRING", "1")
        monkeypatch.setenv(name, "from-parent")

        def fail_read(_name: str) -> str:
            raise AssertionError("keyring must not be read when CYT_SKIP_KEYRING is set")

        monkeypatch.setattr("cyt.launch.secrets._read_keyring", fail_read)

        value, source = resolve_credential(name)

        assert value == "from-parent"
        assert source == "env: process"

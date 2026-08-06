"""Tests for hook daemon credential inspection and setup."""

from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.hook import credentials as hook_credentials
from cyt.hook import daemon as hook_daemon
from cyt.launch.secrets import clear_keyring_cache
from tests.support.credential_helpers import (
    env_file_source_label,
    install_test_pre_dotenv,
    isolate_credential_env_paths,
)

EXECUTOR_TOKEN = "EXECUTOR_TOKEN"
EXECUTOR_TOKEN_VALUE = "exec-" + "token"


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
    return isolate_credential_env_paths(monkeypatch, tmp_path)


def _executor_config(*, tools_from: str = "executor") -> dict:
    return {
        "network": {"proxy": {"reverse": {"port": 8834}}},
        "skills": {"enabled": False},
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "pipeline": "bm25",
            "tools": {
                "sequence": ["bm25"],
                "hook": {
                    "tools_from": tools_from,
                    "executor_token_var": EXECUTOR_TOKEN,
                },
            },
        },
    }


def test_required_hook_daemon_env_var_names_includes_executor_token() -> None:
    config = _executor_config()
    names = hook_credentials.required_hook_daemon_env_var_names(config)
    assert EXECUTOR_TOKEN in names


def test_required_hook_daemon_env_var_names_definitions_mode() -> None:
    config = _executor_config(tools_from="definitions")
    names = hook_credentials.required_hook_daemon_env_var_names(config)
    assert EXECUTOR_TOKEN not in names


def test_report_and_ensure_hook_credentials_executor_token_in_keyring(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _executor_config()
    with patch(
        "cyt.hook.credentials.inspect_named_credentials",
        return_value=[(EXECUTOR_TOKEN, "keyring")],
    ):
        sources = hook_credentials.report_and_ensure_hook_credentials(config)

    out = capsys.readouterr().out
    assert "Executor tool catalog: enabled (EXECUTOR_TOKEN)" in out
    assert f"  {EXECUTOR_TOKEN}: keyring" in out
    assert "All required keys are already available." in out
    assert sources == {EXECUTOR_TOKEN: "keyring"}


def test_report_and_ensure_hook_credentials_prompts_on_tty(
    isolated_env_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _executor_config()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
    monkeypatch.setattr(
        "cyt.launch.secrets.getpass.getpass",
        lambda _prompt: EXECUTOR_TOKEN_VALUE,
    )
    monkeypatch.setattr("cyt.launch.secrets._write_keyring", lambda _key, _value: True)

    sources = hook_credentials.report_and_ensure_hook_credentials(config)

    out = capsys.readouterr().out
    assert f"  {EXECUTOR_TOKEN}: missing" in out
    assert "Updated credentials:" in out
    assert sources[EXECUTOR_TOKEN] == "keyring"


def test_report_and_ensure_hook_credentials_writes_env_when_keyring_fails(
    isolated_env_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cyt.config as configs
    import cyt.hook.credentials as hook_credentials_module

    config = _executor_config()
    user_env = isolated_env_paths["user_env"]
    monkeypatch.setattr(configs, "USER_ENV_PATH", user_env)
    monkeypatch.setattr(hook_credentials_module, "USER_ENV_PATH", user_env)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
    monkeypatch.setattr(
        "cyt.launch.secrets.getpass.getpass",
        lambda _prompt: EXECUTOR_TOKEN_VALUE,
    )
    monkeypatch.setattr("cyt.launch.secrets._write_keyring", lambda _key, _value: False)

    sources = hook_credentials.report_and_ensure_hook_credentials(config)

    assert sources[EXECUTOR_TOKEN] == env_file_source_label(user_env)
    assert user_env.read_text(encoding="utf-8") == f"{EXECUTOR_TOKEN}={EXECUTOR_TOKEN_VALUE}\n"


def test_report_and_ensure_hook_credentials_non_tty_warns_without_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _executor_config()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with patch(
        "cyt.hook.credentials.inspect_named_credentials",
        return_value=[(EXECUTOR_TOKEN, None)],
    ):
        hook_credentials.report_and_ensure_hook_credentials(
            config,
            exit_on_missing_non_tty=False,
        )

    out = capsys.readouterr().out
    assert f"\t{EXECUTOR_TOKEN}" in out
    assert "Or run interactively to store them in the keyring." in out


def test_report_and_ensure_hook_credentials_non_tty_exits_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _executor_config()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with (
        patch(
            "cyt.hook.credentials.inspect_named_credentials",
            return_value=[(EXECUTOR_TOKEN, None)],
        ),
        pytest.raises(SystemExit, match=EXECUTOR_TOKEN),
    ):
        hook_credentials.report_and_ensure_hook_credentials(
            config,
            exit_on_missing_non_tty=True,
        )


def test_daemon_status_checks_credentials_before_running_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _executor_config()
    with (
        patch("cyt.hook.daemon.load_config", return_value=config),
        patch("cyt.hook.daemon.read_hook_daemon_pidfile", return_value=None),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
        patch(
            "cyt.hook.credentials.report_and_ensure_hook_credentials",
            return_value={EXECUTOR_TOKEN: "keyring"},
        ) as ensure,
    ):
        hook_daemon.daemon_status()

    ensure.assert_called_once_with(config, exit_on_missing_non_tty=False)
    captured = capsys.readouterr()
    assert "hook daemon: running" in captured.err


def test_daemon_status_non_tty_missing_token_still_prints_daemon_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _executor_config()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with (
        patch("cyt.hook.daemon.load_config", return_value=config),
        patch("cyt.hook.daemon.read_hook_daemon_pidfile", return_value=None),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=None),
        patch(
            "cyt.hook.credentials.inspect_named_credentials",
            return_value=[(EXECUTOR_TOKEN, None)],
        ),
    ):
        hook_daemon.daemon_status()

    captured = capsys.readouterr()
    assert f"\t{EXECUTOR_TOKEN}" in captured.out
    assert captured.err.strip() == "hook daemon: not running"


def test_daemon_status_definitions_mode_no_executor_banner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _executor_config(tools_from="definitions")
    config["pruning"]["tools"]["sequence"] = ["bm25"]
    with (
        patch("cyt.hook.daemon.load_config", return_value=config),
        patch("cyt.hook.daemon.read_hook_daemon_pidfile", return_value=None),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=None),
    ):
        hook_daemon.daemon_status()

    out = capsys.readouterr().out
    assert "Executor tool catalog" not in out

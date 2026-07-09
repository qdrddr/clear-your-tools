"""Shared helpers for credential-resolution tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt import config as configs

DEFAULT_CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "OPENROUTER_" + "API_KEY",
    "ANTHROPIC_" + "API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_" + "API_KEY",
    "CODEX_OPENAI_" + "API_KEY",
    "DEEPINFRA_" + "API_KEY",
)

_test_pre_dotenv: dict[str, str] = {}


def env_file_source_label(path: Path) -> str:
    """Expected credential source label for an isolated env file path."""
    from cyt.launch.secrets import env_file_source_label as label

    return label(path)


def process_env_before_dotenv_for_tests() -> dict[str, str]:
    """Return the mutable pre-dotenv snapshot used by credential tests."""
    return _test_pre_dotenv


def isolate_credential_env_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    chdir: bool = True,
) -> dict[str, Path]:
    """Point env-file lookups at empty temp paths."""
    work_dir = tmp_path / "credential-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    user_env = tmp_path / "home" / ".config" / "cyt" / ".env"
    cwd_env = work_dir / ".env"
    monkeypatch.setattr(configs, "cwd_env_path", lambda: cwd_env)
    monkeypatch.setattr(configs, "USER_ENV_PATH", user_env)
    if chdir:
        monkeypatch.chdir(work_dir)
    return {
        "work_dir": work_dir,
        "cwd_env": cwd_env,
        "user_env": user_env,
    }


def install_test_pre_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Track ``monkeypatch.setenv`` as true shell exports for credential resolution."""
    _test_pre_dotenv.clear()
    monkeypatch.setattr(
        configs,
        "process_env_before_dotenv",
        process_env_before_dotenv_for_tests,
    )
    original_setenv = monkeypatch.setenv

    def tracking_setenv(name: str, value: str, prepend: str | None = None) -> None:
        _test_pre_dotenv[str(name)] = str(value)
        original_setenv(name, value, prepend)

    monkeypatch.setattr(monkeypatch, "setenv", tracking_setenv)


@pytest.fixture
def isolated_credential_env_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    return isolate_credential_env_paths(monkeypatch, tmp_path)

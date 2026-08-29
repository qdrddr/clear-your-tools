"""Tests for cyt_client.hook_executable."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cyt_client.hook_executable import (
    build_installed_cyt_client_command,
    build_installed_cyt_daemon_start_command,
    build_uv_run_dev_command,
    is_uv_run_dev_hook_command,
    quote_for_cmd_exe,
    repo_root_from_uv_run_hook_command,
    resolve_hook_executable,
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only quoting")
def test_build_uv_run_dev_command_uses_absolute_uv_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uv_path = Path(r"C:\Users\me\.local\bin\uv.exe")
    monkeypatch.setattr(
        "cyt_client.hook_executable.resolve_hook_executable",
        lambda name: str(uv_path) if name == "uv" else name,
    )
    command = build_uv_run_dev_command(
        Path(r"C:\Users\me\git\clear-your-tools"),
        "src/cyt_client/cli.py",
    )
    assert command.startswith(f'"{uv_path}" run --directory "')
    assert "src/cyt_client/cli.py" in command


def test_is_uv_run_dev_hook_command_accepts_absolute_uv_path() -> None:
    command = (
        r'"C:\Users\me\.local\bin\uv.exe" run --directory '
        r'"C:\repo" src/cyt_client/cli.py'
    )
    assert is_uv_run_dev_hook_command(command)


def test_repo_root_from_uv_run_hook_command_accepts_absolute_uv_path() -> None:
    command = (
        r'"C:\Users\me\.local\bin\uv.exe" run --directory '
        r'"C:\Users\me\git\clear-your-tools" src/cyt_client/cli.py'
    )
    assert repo_root_from_uv_run_hook_command(command) == Path(
        r"C:\Users\me\git\clear-your-tools",
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only quoting")
def test_build_installed_commands_quote_absolute_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_resolve(name: str) -> str:
        mapping = {
            "cyt-client": r"C:\Users\me\.local\bin\cyt-client.exe",
            "cyt": r"C:\Users\me\.local\bin\cyt.exe",
        }
        return mapping.get(name, name)

    monkeypatch.setattr("cyt_client.hook_executable.resolve_hook_executable", fake_resolve)
    assert build_installed_cyt_client_command() == r'"C:\Users\me\.local\bin\cyt-client.exe"'
    assert build_installed_cyt_daemon_start_command() == (
        r'"C:\Users\me\.local\bin\cyt.exe" hook daemon start --unattended'
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only quoting")
def test_quote_for_cmd_exe_quotes_windows_drive_paths() -> None:
    assert quote_for_cmd_exe(r"C:\tools\uv.exe") == r'"C:\tools\uv.exe"'


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only fallback")
def test_resolve_hook_executable_falls_back_to_common_windows_locations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    uv_path = tmp_path / ".local" / "bin" / "uv.exe"
    uv_path.parent.mkdir(parents=True)
    uv_path.write_text("", encoding="utf-8")
    monkeypatch.setattr("cyt_client.hook_executable.shutil.which", lambda _name: None)
    monkeypatch.setattr("cyt_client.hook_executable.Path.home", lambda: tmp_path)
    assert resolve_hook_executable("uv") == str(uv_path.resolve())

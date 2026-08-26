"""Tests for cross-platform process helpers."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from cyt.platform import process as platform_process


def test_find_listen_pid_unix_parses_lsof_output() -> None:
    lsof_output = "12345\n"
    with (
        patch.object(sys, "platform", "linux"),
        patch.object(
            platform_process.subprocess,
            "run",
            return_value=type("R", (), {"returncode": 0, "stdout": lsof_output})(),
        ),
    ):
        assert platform_process.find_listen_pid(8834) == 12345


def test_find_listen_pid_windows_parses_netstat_output() -> None:
    netstat_output = (
        "  TCP    127.0.0.1:8834         0.0.0.0:0              LISTENING       54321\n"
    )
    with (
        patch.object(sys, "platform", "win32"),
        patch.object(
            platform_process.subprocess,
            "run",
            return_value=type("R", (), {"returncode": 0, "stdout": netstat_output})(),
        ),
    ):
        assert platform_process.find_listen_pid(8834) == 54321


def test_list_process_command_lines_unix() -> None:
    ps_output = "  100 cyt proxy --port 8834\n  200 other command\n"
    with (
        patch.object(sys, "platform", "linux"),
        patch.object(
            platform_process.subprocess,
            "run",
            return_value=type("R", (), {"returncode": 0, "stdout": ps_output})(),
        ),
    ):
        pairs = platform_process.list_process_command_lines()
    assert pairs == [(100, "cyt proxy --port 8834"), (200, "other command")]


def test_terminate_process_windows_uses_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(platform_process, "pid_alive", lambda _pid: False)
    with patch.object(sys, "platform", "win32"):
        with patch.object(platform_process.subprocess, "run", side_effect=fake_run):
            platform_process.terminate_process(999)
    assert calls
    assert calls[0][:3] == ["taskkill", "/PID", "999"]


def test_process_start_time_unix_parses_ps_output() -> None:
    ps_output = "Wed Aug 26 12:00:00 2026\n"
    with (
        patch.object(sys, "platform", "linux"),
        patch.object(
            platform_process.subprocess,
            "run",
            return_value=type("R", (), {"returncode": 0, "stdout": ps_output})(),
        ),
    ):
        started = platform_process.process_start_time(12345)
    assert started is not None
    assert started.year == 2026
    assert started.month == 8
    assert started.day == 26
    assert started.hour == 12


def test_process_start_time_windows_parses_powershell_output() -> None:
    ps_output = "2026-08-26T16:00:00.0000000Z\n"
    with (
        patch.object(sys, "platform", "win32"),
        patch.object(
            platform_process.subprocess,
            "run",
            return_value=type("R", (), {"returncode": 0, "stdout": ps_output})(),
        ),
    ):
        started = platform_process.process_start_time(54321)
    assert started is not None
    assert started.year == 2026
    assert started.month == 8
    assert started.day == 26

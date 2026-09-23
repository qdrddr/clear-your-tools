"""Tests for quiet stop CLI routing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import cyt.hook.daemon as hook_daemon_mod
import tests.support.bootstrap_env  # noqa: F401
from cyt.cli.exit import QUIET_STOP_EXIT_CODE, parse_quiet_cli_flags, run_quiet_stop
from cyt.cli.router import main as router_main

REPO = Path(__file__).resolve().parents[3]
PROXY_SHIM = REPO / "src" / "cyt" / "proxy" / "cli.py"
ENV = {
    **dict(__import__("os").environ),
    "PYTHONPATH": str(REPO / "src"),
    "CYT_NO_AUTO_BOOTSTRAP": "1",
}


def test_parse_quiet_cli_flags() -> None:
    verbose, config_path = parse_quiet_cli_flags(
        ["--verbose", "--config", "/tmp/config.yaml", "ignored"],
    )
    assert verbose is True
    assert config_path == Path("/tmp/config.yaml")


def test_run_quiet_stop_exits_without_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    def _fail() -> None:
        raise RuntimeError("stop failed")

    with pytest.raises(SystemExit) as exc_info:
        run_quiet_stop(_fail, verbose=False)

    assert exc_info.value.code == QUIET_STOP_EXIT_CODE
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err == ""


def test_run_quiet_stop_verbose_prints_message(capsys: pytest.CaptureFixture[str]) -> None:
    def _fail() -> None:
        raise RuntimeError("stop failed")

    with pytest.raises(SystemExit):
        run_quiet_stop(_fail, verbose=True)

    captured = capsys.readouterr()
    assert captured.err.strip() == "cyt: stop failed"
    assert "Traceback" not in captured.err


def test_router_hook_daemon_stop_is_quiet_on_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch.object(
        hook_daemon_mod,
        "daemon_stop",
        side_effect=RuntimeError("daemon stop failed"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            router_main(["hook", "daemon", "stop"])

    assert exc_info.value.code == QUIET_STOP_EXIT_CODE
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err


def test_proxy_cli_hook_daemon_stop_is_quiet_on_failure() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; os.environ.setdefault('CYT_NO_AUTO_BOOTSTRAP', '1');"
                "from unittest.mock import patch;"
                "import cyt.hook.daemon as daemon_mod;"
                "from cyt.cli.router import main as router_main;"
                "patch.object(daemon_mod, 'daemon_stop', side_effect=RuntimeError('boom')).start();"
                "router_main(['hook', 'daemon', 'stop'])"
            ),
        ],
        capture_output=True,
        text=True,
        env=ENV,
        cwd=REPO,
        check=False,
    )

    assert result.returncode == QUIET_STOP_EXIT_CODE
    assert "Traceback" not in result.stderr


def test_proxy_cli_hook_daemon_stop_entrypoint_has_no_traceback() -> None:
    result = subprocess.run(
        [sys.executable, str(PROXY_SHIM), "hook", "daemon", "stop"],
        capture_output=True,
        text=True,
        env=ENV,
        check=False,
    )

    assert result.returncode == 0
    assert "Traceback" not in result.stderr

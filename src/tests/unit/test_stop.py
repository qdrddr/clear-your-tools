"""Tests for ``cyt stop``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cyt import stop as cyt_stop


def test_stop_all_stops_hook_daemon_then_remaining_proxies() -> None:
    with (
        patch("cyt.hook.daemon.daemon_stop") as daemon_stop,
        patch("cyt.stop.stop_proxies_only") as stop_proxies,
    ):
        cyt_stop.stop_all(verbose=True, config_path=Path("/tmp/config.yaml"))

    daemon_stop.assert_called_once_with(verbose=True, config_path=Path("/tmp/config.yaml"))
    stop_proxies.assert_called_once_with(verbose=True)


def test_stop_cli_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["cyt", "stop", "--verbose"])
    called_verbose: bool | None = None
    called_config_path: Path | None | object = object()

    def fake_stop_all(*, verbose: bool = False, config_path: Path | None = None) -> None:
        nonlocal called_verbose, called_config_path
        called_verbose = verbose
        called_config_path = config_path

    monkeypatch.setattr("cyt.stop.stop_all", fake_stop_all)

    from cyt.proxy.cli_impl import main

    main()
    assert called_verbose is True
    assert called_config_path is None


def test_stop_running_proxies_terminates_matching_pids() -> None:
    with (
        patch("cyt.stop._find_cyt_proxy_pids", return_value=[111, 222]),
        patch("cyt.hook.daemon._pid_alive", side_effect=lambda pid: pid == 111),
        patch("cyt.hook.daemon._process_matches_cyt_proxy", return_value=True),
        patch("cyt.hook.daemon._terminate_pid") as terminate,
    ):
        stopped = cyt_stop.stop_running_proxies(verbose=True)

    assert stopped is True
    terminate.assert_called_once_with(111)


def test_stop_running_proxies_returns_false_when_none_found() -> None:
    with patch("cyt.stop._find_cyt_proxy_pids", return_value=[]):
        assert cyt_stop.stop_running_proxies(verbose=False) is False


def test_find_cyt_proxy_pids_parses_ps_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cyt.stop.os.getpid", lambda: 999)
    ps_output = "\n".join(
        [
            " 111 /usr/bin/python -m cyt.proxy.cli proxy --port 8834",
            " 222 /usr/bin/python -m cyt.proxy.cli stats totals",
            " 999 /usr/bin/python -m cyt.proxy.cli stop",
        ],
    )
    with patch("cyt.stop.subprocess.run", return_value=type("R", (), {"stdout": ps_output})()):
        pids = cyt_stop._find_cyt_proxy_pids()

    assert pids == [111]

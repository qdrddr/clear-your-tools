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


def test_is_cyt_proxy_command_matches_module_and_script_paths() -> None:
    assert cyt_stop.is_cyt_proxy_command("/usr/bin/python -m cyt.proxy.cli proxy --port 8834")
    assert cyt_stop.is_cyt_proxy_command("/usr/bin/python -m cyt.proxy.cli_impl proxy --port 8834")
    assert cyt_stop.is_cyt_proxy_command("uv run src/cyt/proxy/cli.py proxy --port 8840")
    assert not cyt_stop.is_cyt_proxy_command("/usr/bin/python -m cyt.proxy.cli hook cursor")
    assert not cyt_stop.is_cyt_proxy_command("/usr/bin/python -m cyt.proxy.cli stats totals")


def test_stop_running_proxies_terminates_all_matching_pids() -> None:
    with (
        patch("cyt.stop._collect_proxy_pids", return_value=[111, 222]),
        patch("cyt.hook.daemon._terminate_pid") as terminate,
    ):
        stopped = cyt_stop.stop_running_proxies(verbose=True)

    assert stopped is True
    assert terminate.call_args_list == [((111,),), ((222,),)]


def test_stop_running_proxies_terminates_matching_pids() -> None:
    with (
        patch("cyt.stop._collect_proxy_pids", return_value=[111]),
        patch("cyt.hook.daemon._terminate_pid") as terminate,
    ):
        stopped = cyt_stop.stop_running_proxies(verbose=True)

    assert stopped is True
    terminate.assert_called_once_with(111)


def test_stop_running_proxies_returns_false_when_none_found() -> None:
    with patch("cyt.stop._collect_proxy_pids", return_value=[]):
        assert cyt_stop.stop_running_proxies(verbose=False) is False


def test_stop_tracked_proxies_stops_all_registry_entries() -> None:
    entries = [
        {"pid": 111, "port": 8834},
        {"pid": 222, "port": 8840},
    ]
    with (
        patch("cyt.runtime_registry.read_proxy_entries", return_value=entries),
        patch("cyt.stop._hook_daemon_exclude_sets", return_value=(set(), set())),
        patch("cyt.stop._stop_proxy_registry_entry", side_effect=[True, True]) as stop_entry,
    ):
        stopped = cyt_stop.stop_tracked_proxies(verbose=True)

    assert stopped is True
    assert stop_entry.call_count == 2


def test_proxies_are_running_true_for_tracked_alive_proxy() -> None:
    with (
        patch(
            "cyt.runtime_registry.read_proxy_entries",
            return_value=[{"pid": 111, "port": 8834}],
        ),
        patch("cyt.stop._hook_daemon_exclude_sets", return_value=(set(), set())),
        patch("cyt.hook.daemon._pid_alive", return_value=True),
        patch("cyt.hook.daemon._process_matches_cyt_proxy", return_value=True),
        patch("cyt.stop._collect_proxy_pids", return_value=[]),
    ):
        assert cyt_stop.proxies_are_running() is True


def test_proxies_are_running_false_when_none_found() -> None:
    with (
        patch("cyt.stop._hook_daemon_exclude_sets", return_value=(set(), set())),
        patch("cyt.runtime_registry.read_proxy_entries", return_value=[]),
        patch("cyt.stop._collect_proxy_pids", return_value=[]),
    ):
        assert cyt_stop.proxies_are_running() is False


def test_proxy_registry_has_live_servers_true_when_listener_present() -> None:
    with (
        patch(
            "cyt.runtime_registry.read_proxy_entries",
            return_value=[{"pid": 69783, "port": 8835}],
        ),
        patch("cyt.stop._proxy_listener_pid", return_value=69783),
    ):
        assert cyt_stop.proxy_registry_has_live_servers() is True


def test_stop_proxy_registry_servers_stops_hook_daemon_overlap() -> None:
    entries = [
        {"pid": 66292, "port": 8834},
        {"pid": 69783, "port": 8835},
    ]
    with (
        patch("cyt.runtime_registry.read_proxy_entries", return_value=entries),
        patch("cyt.stop._stop_proxy_registry_entry", side_effect=[False, True]) as stop_entry,
    ):
        stopped = cyt_stop.stop_proxy_registry_servers(verbose=True)

    assert stopped is True
    assert stop_entry.call_count == 2
    assert stop_entry.call_args_list[1].kwargs["exclude_pids"] == set()


def test_proxies_conflicting_with_hook_setup_detects_proxy_registry_overlap() -> None:
    with (
        patch(
            "cyt.runtime_registry.read_proxy_entries",
            return_value=[{"pid": 69783, "port": 8835}],
        ),
        patch("cyt.stop.proxy_registry_has_live_servers", return_value=True),
        patch("cyt.stop._collect_proxy_pids", return_value=[]),
    ):
        assert cyt_stop.proxies_conflicting_with_hook_setup() is True


def test_find_cyt_proxy_pids_parses_ps_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cyt.stop.os.getpid", lambda: 999)
    with patch(
        "cyt.platform.process.list_process_command_lines",
        return_value=[
            (111, "/usr/bin/python -m cyt.proxy.cli proxy --port 8834"),
            (112, "uv run src/cyt/proxy/cli.py proxy --port 8840"),
            (222, "/usr/bin/python -m cyt.proxy.cli stats totals"),
            (999, "/usr/bin/python -m cyt.proxy.cli stop"),
        ],
    ):
        pids = cyt_stop._find_cyt_proxy_pids()

    assert pids == [111, 112]

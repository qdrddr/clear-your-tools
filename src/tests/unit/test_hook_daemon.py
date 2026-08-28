"""Tests for cyt hook daemon lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import cyt.proxy.cli  # noqa: F401 — preload runtime imports used by daemon_start
from cyt.hook import daemon as hook_daemon

_REAL_SPAWN_HOOK_SERVER = hook_daemon._spawn_hook_server

OR_KEY = "OPENROUTER_" + "API_KEY"
OR_TOKEN = "or-" + "token"


@pytest.fixture
def pidfile_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "pid.json"
    legacy_hook = tmp_path / "hook-daemon.json"
    legacy_proxy = tmp_path / "proxy.json"
    monkeypatch.setattr("cyt.runtime_registry.PID_REGISTRY", path)
    monkeypatch.setattr("cyt.runtime_registry.HOOK_DAEMON_REGISTRY", path)
    monkeypatch.setattr("cyt.runtime_registry.PROXY_REGISTRY", path)
    monkeypatch.setattr("cyt.runtime_registry.HOOK_DAEMON_PIDFILE", path)
    monkeypatch.setattr("cyt.runtime_registry.LEGACY_HOOK_DAEMON_REGISTRY", legacy_hook)
    monkeypatch.setattr("cyt.runtime_registry.LEGACY_PROXY_REGISTRY", legacy_proxy)
    return path


def test_daemon_start_reuses_existing_server(pidfile_path: Path) -> None:
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=False),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
    ):
        result = hook_daemon.daemon_start(verbose=False)

    assert result.reused is True
    assert result.port == 8834
    payload = json.loads(pidfile_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert payload[-1]["reused"] is True
    assert payload[-1]["pid"] is None


def test_daemon_start_prints_status_when_not_unattended(
    pidfile_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=False),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
        patch("cyt.hook.daemon.report_cyt_mcp_hook_readiness"),
        patch("cyt.hook.daemon.report_mcpc_hook_readiness"),
        patch("cyt.hook.daemon.report_cloudflare_hook_readiness"),
    ):
        hook_daemon.daemon_start(verbose=False, unattended=False)

    err = capsys.readouterr().err.strip()
    assert err.startswith(
        "hook daemon: running pid=null (reused) port=8834 url=http://127.0.0.1:8834/hook/connect started=",
    )


def test_daemon_start_is_silent_when_unattended(
    pidfile_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=False),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
    ):
        hook_daemon.daemon_start(verbose=False, unattended=True)

    assert capsys.readouterr().err == ""


def test_unattended_suppresses_mcpc_logging_warnings(
    pidfile_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(message)s", force=True)
    config = {
        "network": {"proxy": {"reverse": {"port": 8834}}},
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {
                "enabled": True,
                "hook": {"tools_from": "mcpc", "mcpc": {"executable": "mcpc"}},
            },
        },
        "cache": {"enabled": True},
    }

    def _warm_with_mcpc_warnings(_cfg: dict[str, object]) -> None:
        from cyt.mcpc.cli import run_mcpc_json

        run_mcpc_json("mcpc", ["@codebase-memory", "skills-list"])

    with (
        patch("cyt.hook.daemon.load_config", return_value=config),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=False),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
        patch("cyt.cache.schedule_warm_caches", side_effect=_warm_with_mcpc_warnings),
    ):
        hook_daemon.daemon_start(verbose=False, unattended=True)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "mcpc json command failed" not in captured.out


def test_daemon_start_unattended_reuses_without_credential_restart(
    pidfile_path: Path,
) -> None:
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=True),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
        patch("cyt.hook.daemon._stop_hook_server_on_port") as stop,
        patch("cyt.hook.daemon.report_cyt_mcp_hook_readiness") as cyt_mcp_ready,
        patch("cyt.hook.daemon.report_mcpc_hook_readiness") as mcpc_ready,
        patch("cyt.hook.daemon.report_cloudflare_hook_readiness") as cf_ready,
        patch("cyt.hook.daemon._schedule_warm_caches") as warm,
    ):
        result = hook_daemon.daemon_start(verbose=False, unattended=True)

    stop.assert_not_called()
    cyt_mcp_ready.assert_not_called()
    mcpc_ready.assert_not_called()
    cf_ready.assert_not_called()
    warm.assert_not_called()
    assert result.reused is True
    assert result.port == 8834


def test_daemon_start_restarts_reused_server_when_credentials_required(
    pidfile_path: Path,
) -> None:
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=True),
        patch(
            "cyt.hook.daemon._resolve_spawn_credentials",
            return_value={OR_KEY: OR_TOKEN},
        ),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
        patch("cyt.hook.daemon._stop_hook_server_on_port", return_value=True) as stop,
        patch("cyt.hook.daemon._find_spawn_port", return_value=8835),
        patch("cyt.hook.daemon._spawn_hook_server") as spawn,
        patch("cyt.hook.daemon._wait_for_hook_server", return_value=True),
    ):
        spawn.return_value = MagicMock(pid=12345)
        result = hook_daemon.daemon_start(verbose=False)

    stop.assert_called_once_with(8834, verbose=False, force=True)
    spawn.assert_called_once()
    assert spawn.call_args.kwargs["extra_env"] == {OR_KEY: OR_TOKEN}
    assert result.reused is False
    assert result.port == 8835


def test_daemon_start_reuses_cyt_spawned_server_with_credentials(
    pidfile_path: Path,
) -> None:
    pidfile_path.write_text(
        json.dumps(
            [
                {
                    "pid": 12345,
                    "port": 8834,
                    "hook_url": "http://127.0.0.1:8834/hook/connect",
                    "owner": "cyt-hook-daemon",
                    "reused": False,
                    "credentials_injected": True,
                },
            ],
        ),
        encoding="utf-8",
    )
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=True),
        patch(
            "cyt.hook.daemon._resolve_spawn_credentials",
            return_value={OR_KEY: OR_TOKEN},
        ),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
        patch("cyt.hook.daemon._stop_hook_server_on_port") as stop,
    ):
        result = hook_daemon.daemon_start(verbose=False)

    stop.assert_not_called()
    assert result.reused is True
    assert result.port == 8834


def test_spawn_hook_server_passes_resolved_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OR_KEY, OR_TOKEN)
    monkeypatch.setattr(hook_daemon, "_spawn_hook_server", _REAL_SPAWN_HOOK_SERVER)
    with patch("cyt.hook.daemon.subprocess.Popen") as popen:
        popen.return_value = MagicMock()
        hook_daemon._spawn_hook_server(
            port=8834,
            config_path=None,
            verbose=False,
            extra_env={OR_KEY: OR_TOKEN},
        )

    _, kwargs = popen.call_args
    assert kwargs["env"][OR_KEY] == OR_TOKEN
    assert kwargs["env"]["CYT_SKIP_KEYRING"] == "1"


def test_resolve_spawn_credentials_exports_pipeline_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OR_KEY, OR_TOKEN)

    def fake_resolve(
        config: dict,
        *,
        allow_prompt: bool = False,
        require_all: bool = True,
    ) -> dict[str, str]:
        assert allow_prompt is False
        assert require_all is True
        return {OR_KEY: OR_TOKEN}

    with patch(
        "cyt.hook.daemon.resolve_hook_daemon_child_env",
        side_effect=fake_resolve,
    ) as resolve:
        extra = hook_daemon._resolve_spawn_credentials({})

    resolve.assert_called_once()
    assert extra == {OR_KEY: OR_TOKEN}


def test_unattended_missing_credentials_reuses_running_server(
    pidfile_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = {
        "network": {"proxy": {"reverse": {"port": 8834}}},
        "pruning": {"tools": {"sequence": ["llm"]}},
    }
    with (
        patch("cyt.hook.daemon.load_config", return_value=config),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=True),
        patch(
            "cyt.hook.daemon.required_proxy_env_var_names",
            return_value=[OR_KEY],
        ),
        patch(
            "cyt.hook.daemon._resolve_spawn_credentials",
            return_value=None,
        ) as resolve,
        patch("cyt.hook.daemon._hook_daemon_has_credentials", return_value=False),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
        patch("cyt.hook.daemon._stop_hook_server_on_port") as stop,
        patch("cyt.hook.daemon._spawn_hook_server") as spawn,
    ):
        result = hook_daemon.daemon_start(verbose=True, unattended=True)

    resolve.assert_not_called()
    stop.assert_not_called()
    spawn.assert_not_called()
    err = capsys.readouterr().err
    assert err == ""
    assert result.reused is True
    assert result.port == 8834


def test_unattended_skips_credential_reinjection(
    pidfile_path: Path,
) -> None:
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=True),
        patch("cyt.hook.daemon._hook_daemon_has_credentials", return_value=True),
        patch(
            "cyt.hook.daemon._resolve_spawn_credentials",
            return_value={OR_KEY: OR_TOKEN},
        ),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
        patch("cyt.hook.daemon._stop_hook_server_on_port") as stop,
        patch("cyt.hook.daemon._spawn_hook_server") as spawn,
    ):
        result = hook_daemon.daemon_start(verbose=False, unattended=True)

    stop.assert_not_called()
    spawn.assert_not_called()
    assert result.reused is True
    assert result.port == 8834


def test_unattended_spawn_timeout_exits_successfully(
    pidfile_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=False),
        patch("cyt.hook.daemon._find_reusable_hook_port", side_effect=[None, None]),
        patch("cyt.hook.daemon._find_spawn_port", return_value=8835),
        patch("cyt.hook.daemon._spawn_hook_server") as spawn,
        patch("cyt.hook.daemon._wait_for_hook_server", return_value=False),
    ):
        spawn.return_value = MagicMock(pid=12345, terminate=MagicMock())
        result = hook_daemon.daemon_start(verbose=False, unattended=True)

    spawn.return_value.terminate.assert_called_once()
    assert capsys.readouterr().err == ""
    assert result.outcome == "already_running"
    assert result.port == 8834


def test_interactive_tty_passes_allow_prompt_to_credential_resolution(
    pidfile_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook_daemon.sys.stdin, "isatty", lambda: True)
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=True),
        patch(
            "cyt.hook.daemon._resolve_spawn_credentials",
            return_value={OR_KEY: OR_TOKEN},
        ) as resolve,
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
        patch("cyt.hook.daemon._stop_hook_server_on_port", return_value=True),
        patch("cyt.hook.daemon._find_spawn_port", return_value=8835),
        patch("cyt.hook.daemon._spawn_hook_server") as spawn,
        patch("cyt.hook.daemon._wait_for_hook_server", return_value=True),
    ):
        spawn.return_value = MagicMock(pid=12345)
        hook_daemon.daemon_start(verbose=False, unattended=False)

    resolve.assert_called_once_with(
        {"network": {"proxy": {"reverse": {"port": 8834}}}},
        allow_prompt=True,
        require_all=True,
    )


def test_daemon_stop_clears_reused_pidfile(pidfile_path: Path) -> None:
    pidfile_path.write_text(
        json.dumps(
            [
                {
                    "pid": None,
                    "port": 8834,
                    "hook_url": "http://127.0.0.1:8834/hook/connect",
                    "reused": True,
                },
            ],
        ),
        encoding="utf-8",
    )
    with patch("cyt.hook.daemon._stop_hook_port", return_value=True):
        hook_daemon.daemon_stop(verbose=False)
    assert not pidfile_path.exists()


def test_daemon_stop_kills_reused_hook_server_on_port(pidfile_path: Path) -> None:
    pidfile_path.write_text(
        json.dumps([{"pid": None, "reused": True, "port": 8834}]),
        encoding="utf-8",
    )
    with patch("cyt.hook.daemon._stop_hook_port", return_value=True) as stop:
        hook_daemon.daemon_stop(verbose=True)
        assert stop.call_count >= 1
        stop.assert_any_call(8834, verbose=True)
    assert not pidfile_path.exists()


def test_daemon_restart_stops_then_starts(pidfile_path: Path) -> None:
    with (
        patch("cyt.hook.daemon._daemon_stop_locked") as stop,
        patch("cyt.hook.daemon._daemon_start_locked") as start,
    ):
        start.return_value = hook_daemon.HookDaemonStartResult(
            outcome="spawned",
            port=8835,
            hook_url="http://127.0.0.1:8835/hook/connect",
            pid=12345,
            reused=False,
        )
        result = hook_daemon.daemon_restart(verbose=True, unattended=True)

    stop.assert_called_once_with(verbose=True, config_path=None)
    start.assert_called_once_with(
        config_path=None,
        verbose=True,
        foreground=False,
        unattended=True,
        force_spawn=True,
        ports_already_stopped=True,
    )
    assert result.port == 8835
    assert result.reused is False


def test_daemon_restart_prints_status_when_not_unattended(
    pidfile_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("cyt.hook.daemon.daemon_stop"),
        patch("cyt.hook.daemon._wait_for_port_free", return_value=True),
        patch("cyt.hook.daemon._stop_hook_server_on_port"),
        patch("cyt.hook.daemon.read_hook_daemon_entries", return_value=[]),
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=False),
        patch("cyt.hook.daemon._find_reusable_hook_port", side_effect=[None, None]),
        patch("cyt.hook.daemon._find_spawn_port", return_value=8835),
        patch("cyt.hook.daemon._spawn_hook_server") as spawn,
        patch("cyt.hook.daemon._wait_for_hook_server", return_value=True),
        patch("cyt.hook.daemon.report_cyt_mcp_hook_readiness"),
        patch("cyt.hook.daemon.report_mcpc_hook_readiness"),
        patch("cyt.hook.daemon.report_cloudflare_hook_readiness"),
    ):
        spawn.return_value = MagicMock(pid=12345)
        hook_daemon.daemon_restart(verbose=False, unattended=False)

    err = capsys.readouterr().err.strip()
    assert err.startswith("hook daemon: restarting...")
    assert (
        "hook daemon: running pid=12345 port=8835 url=http://127.0.0.1:8835/hook/connect started="
        in err
    )


def test_daemon_stop_without_pidfile_uses_config_port() -> None:
    with (
        patch("cyt.hook.daemon.read_hook_daemon_entries", return_value=[]),
        patch("cyt.hook.daemon._resolve_stop_ports", return_value=[8834]),
        patch("cyt.hook.daemon._stop_hook_port", return_value=True) as stop,
    ):
        hook_daemon.daemon_stop(verbose=True)
        stop.assert_called_once_with(8834, verbose=True)


def test_needs_credential_injection_includes_executor_token() -> None:
    config = {
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {
                "hook": {
                    "tools_from": "executor",
                    "executor_token_var": "EXECUTOR_TOKEN",
                },
            },
        },
    }
    assert hook_daemon._needs_credential_injection(config) is True


def test_daemon_status_includes_started_timestamp(capsys: pytest.CaptureFixture[str]) -> None:
    source = {"started_at": "2026-08-26T16:00:00+00:00", "pid": None, "reused": False}
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=False),
        patch("cyt.hook.daemon.read_hook_daemon_pidfile", return_value=source),
        patch("cyt.hook.daemon.read_hook_daemon_entries", return_value=[source]),
        patch("cyt.hook.daemon.find_hook_daemon_entry_for_port", return_value=source),
        patch("cyt.hook.daemon.find_hook_port_for_status", return_value=8834),
        patch("cyt.hook.daemon.report_cyt_mcp_hook_readiness"),
        patch("cyt.hook.daemon.report_mcpc_hook_readiness"),
        patch("cyt.hook.daemon.report_cloudflare_hook_readiness"),
    ):
        hook_daemon.daemon_status()

    err = capsys.readouterr().err.strip()
    assert err.startswith(
        "hook daemon: running pid=null port=8834 url=http://127.0.0.1:8834/hook/connect started=",
    )
    assert err.endswith("12:00:00-04:00")


def test_daemon_start_and_status_use_same_started_timestamp(
    pidfile_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry_started_at = "2026-08-26T16:14:44+00:00"
    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=False),
        patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834),
        patch("cyt.hook.daemon.report_cyt_mcp_hook_readiness"),
        patch("cyt.hook.daemon.report_mcpc_hook_readiness"),
        patch("cyt.hook.daemon.report_cloudflare_hook_readiness"),
        patch(
            "cyt.runtime_registry._now_iso",
            return_value=registry_started_at,
        ),
    ):
        hook_daemon.daemon_start(verbose=False, unattended=False)

    start_err = capsys.readouterr().err.strip()
    capsys.readouterr()

    with (
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=False),
        patch("cyt.hook.daemon.read_hook_daemon_pidfile") as read_pidfile,
        patch("cyt.hook.daemon.read_hook_daemon_entries") as read_entries,
        patch("cyt.hook.daemon.find_hook_port_for_status", return_value=8834),
        patch("cyt.hook.daemon.report_cyt_mcp_hook_readiness"),
        patch("cyt.hook.daemon.report_mcpc_hook_readiness"),
        patch("cyt.hook.daemon.report_cloudflare_hook_readiness"),
    ):
        entry = json.loads(pidfile_path.read_text(encoding="utf-8"))[-1]
        read_pidfile.return_value = entry
        read_entries.return_value = [entry]
        hook_daemon.daemon_status()

    status_err = capsys.readouterr().err.strip()
    assert "started=" in start_err
    assert start_err.split("started=", 1)[1] == status_err.split("started=", 1)[1]


def test_find_hook_port_for_status_probes_recorded_ports_before_base() -> None:
    from cyt.hook.port import find_hook_port_for_status

    entries = [{"port": 8840, "hook_url": "http://127.0.0.1:8840/hook/connect"}]
    hook_health = {"name": "cyt", "status": "ok", "hook": True}

    def fake_fetch(port: int, *, timeout: float | None = None) -> dict[str, Any] | None:
        del timeout
        return hook_health if port == 8840 else None

    with patch("cyt.hook.port.fetch_cyt_health", side_effect=fake_fetch) as fetch:
        port = find_hook_port_for_status(8834, entries, None)

    assert port == 8840
    assert {call.args[0] for call in fetch.call_args_list} == {8840, 8834}


def test_find_hook_port_for_status_falls_back_to_base_port() -> None:
    from cyt.hook.port import find_hook_port_for_status

    with patch("cyt.hook.port.fetch_cyt_health", return_value=None) as fetch:
        port = find_hook_port_for_status(8834, [], None)

    assert port is None
    fetch.assert_called_once_with(8834, timeout=0.3)


def test_find_hook_server_port_probes_in_parallel_batches() -> None:
    from cyt.hook.port import find_hook_server_port

    hook_health = {"name": "cyt", "status": "ok", "hook": True}

    def fake_fetch(port: int, *, timeout: float | None = None) -> dict[str, Any] | None:
        del timeout
        return hook_health if port == 8836 else None

    with patch("cyt.hook.port.fetch_cyt_health", side_effect=fake_fetch) as fetch:
        port = find_hook_server_port(8834, max_attempts=20)

    assert port == 8836
    assert fetch.call_count == 20


def test_find_hook_server_port_returns_lowest_match_in_batch() -> None:
    from cyt.hook.port import find_hook_server_port

    hook_health = {"name": "cyt", "status": "ok", "hook": True}

    def fake_fetch(port: int, *, timeout: float | None = None) -> dict[str, Any] | None:
        del timeout
        return hook_health if port in {8835, 8838} else None

    with patch("cyt.hook.port.fetch_cyt_health") as fetch:
        fetch.side_effect = fake_fetch
        port = find_hook_server_port(8834, max_attempts=10)

    assert port == 8835


def test_find_hook_port_for_status_prefers_recorded_port_over_base() -> None:
    from cyt.hook.port import find_hook_port_for_status

    entries = [{"port": 8840, "hook_url": "http://127.0.0.1:8840/hook/connect"}]
    hook_health = {"name": "cyt", "status": "ok", "hook": True}

    with patch("cyt.hook.port.fetch_cyt_health", return_value=hook_health):
        port = find_hook_port_for_status(8834, entries, None)

    assert port == 8840

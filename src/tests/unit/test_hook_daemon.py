"""Tests for cyt hook daemon lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import cyt.proxy.cli  # noqa: F401 — preload runtime imports used by daemon_start
from cyt.hook import daemon as hook_daemon

OR_KEY = "OPENROUTER_" + "API_KEY"
OR_TOKEN = "or-" + "token"


@pytest.fixture
def pidfile_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "hook-daemon.json"
    monkeypatch.setattr("cyt.runtime_registry.HOOK_DAEMON_REGISTRY", path)
    monkeypatch.setattr("cyt.runtime_registry.HOOK_DAEMON_PIDFILE", path)
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
    ):
        hook_daemon.daemon_start(verbose=False, unattended=False)

    err = capsys.readouterr().err.strip()
    assert (
        err
        == "hook daemon: running pid=null (reused) port=8834 url=http://127.0.0.1:8834/hook/inject"
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
            "inject_via": "hook",
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

    stop.assert_called_once_with(8834, verbose=False)
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
                    "hook_url": "http://127.0.0.1:8834/hook/inject",
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


def test_unattended_missing_credentials_restarts_uncredentialed_server(
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
        patch("cyt.hook.daemon._find_spawn_port", return_value=8835),
        patch("cyt.hook.daemon._spawn_hook_server") as spawn,
        patch("cyt.hook.daemon._wait_for_hook_server", return_value=True),
    ):
        spawn.return_value = MagicMock(pid=12345)
        result = hook_daemon.daemon_start(verbose=True, unattended=True)

    resolve.assert_called_once_with(
        config,
        allow_prompt=False,
        require_all=False,
    )
    stop.assert_called_once_with(8834, verbose=False)
    err = capsys.readouterr().err
    assert err == ""
    assert result.reused is False
    assert result.port == 8835


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
                    "hook_url": "http://127.0.0.1:8834/hook/inject",
                    "reused": True,
                },
            ],
        ),
        encoding="utf-8",
    )
    with patch("cyt.hook.daemon._stop_hook_server_on_port", return_value=True):
        hook_daemon.daemon_stop(verbose=False)
    assert not pidfile_path.exists()


def test_daemon_stop_kills_reused_hook_server_on_port(pidfile_path: Path) -> None:
    pidfile_path.write_text(
        json.dumps([{"pid": None, "reused": True, "port": 8834}]),
        encoding="utf-8",
    )
    with patch("cyt.hook.daemon._stop_hook_server_on_port", return_value=True) as stop:
        hook_daemon.daemon_stop(verbose=True)
        stop.assert_called_once_with(8834, verbose=True, label="hook daemon")
    assert not pidfile_path.exists()


def test_daemon_restart_stops_then_starts(pidfile_path: Path) -> None:
    with (
        patch("cyt.hook.daemon.daemon_stop") as stop,
        patch("cyt.hook.daemon.daemon_start") as start,
    ):
        start.return_value = hook_daemon.HookDaemonStartResult(
            outcome="spawned",
            port=8835,
            hook_url="http://127.0.0.1:8835/hook/inject",
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
    )
    assert result.port == 8835
    assert result.reused is False


def test_daemon_restart_prints_status_when_not_unattended(
    pidfile_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("cyt.hook.daemon.daemon_stop"),
        patch(
            "cyt.hook.daemon.load_config",
            return_value={"network": {"proxy": {"reverse": {"port": 8834}}}},
        ),
        patch("cyt.hook.daemon._needs_credential_injection", return_value=False),
        patch("cyt.hook.daemon._find_reusable_hook_port", side_effect=[None, None]),
        patch("cyt.hook.daemon._find_spawn_port", return_value=8835),
        patch("cyt.hook.daemon._spawn_hook_server") as spawn,
        patch("cyt.hook.daemon._wait_for_hook_server", return_value=True),
    ):
        spawn.return_value = MagicMock(pid=12345)
        hook_daemon.daemon_restart(verbose=False, unattended=False)

    err = capsys.readouterr().err.strip()
    assert err == "hook daemon: running pid=12345 port=8835 url=http://127.0.0.1:8835/hook/inject"


def test_daemon_stop_without_pidfile_scans_config_port() -> None:
    with (
        patch("cyt.hook.daemon.read_hook_daemon_entries", return_value=[]),
        patch("cyt.hook.daemon._resolve_stop_ports", return_value=[8834]),
        patch("cyt.hook.daemon._stop_hook_server_on_port", return_value=True) as stop,
    ):
        hook_daemon.daemon_stop(verbose=True)
        stop.assert_called_once_with(8834, verbose=True)


def test_needs_credential_injection_includes_executor_token() -> None:
    config = {
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "hook": {
                    "tools_from": "executor",
                    "executor_token_var": "EXECUTOR_TOKEN",
                },
            },
        },
    }
    assert hook_daemon._needs_credential_injection(config) is True

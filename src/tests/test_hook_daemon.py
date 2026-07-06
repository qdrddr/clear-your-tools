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
    monkeypatch.setattr(hook_daemon, "HOOK_DAEMON_PIDFILE", path)
    monkeypatch.setattr("cyt.hook.port.HOOK_DAEMON_PIDFILE", path)
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
    assert payload["reused"] is True
    assert payload["pid"] is None


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
    ) -> dict[str, str]:
        assert allow_prompt is False
        return {OR_KEY: OR_TOKEN}

    with patch(
        "cyt.hook.daemon.resolve_hook_daemon_child_env",
        side_effect=fake_resolve,
    ) as resolve:
        extra = hook_daemon._resolve_spawn_credentials({})

    resolve.assert_called_once()
    assert extra == {OR_KEY: OR_TOKEN}


def test_daemon_stop_clears_reused_pidfile(pidfile_path: Path) -> None:
    pidfile_path.write_text(
        json.dumps(
            {
                "pid": None,
                "port": 8834,
                "hook_url": "http://127.0.0.1:8834/hook/inject",
                "reused": True,
            },
        ),
        encoding="utf-8",
    )
    with patch("cyt.hook.daemon._stop_hook_server_on_port", return_value=True):
        hook_daemon.daemon_stop(verbose=False)
    assert not pidfile_path.exists()


def test_daemon_stop_kills_reused_hook_server_on_port(pidfile_path: Path) -> None:
    pidfile_path.write_text(
        json.dumps({"pid": None, "reused": True, "port": 8834}),
        encoding="utf-8",
    )
    with patch("cyt.hook.daemon._stop_hook_server_on_port", return_value=True) as stop:
        hook_daemon.daemon_stop(verbose=True)
        stop.assert_called_once_with(8834, verbose=True)
    assert not pidfile_path.exists()


def test_daemon_stop_without_pidfile_scans_config_port() -> None:
    with (
        patch("cyt.hook.daemon.read_hook_daemon_pidfile", return_value=None),
        patch("cyt.hook.daemon._resolve_stop_port", return_value=8834),
        patch("cyt.hook.daemon._stop_hook_server_on_port", return_value=True) as stop,
    ):
        hook_daemon.daemon_stop(verbose=True)
        stop.assert_called_once_with(8834, verbose=True)

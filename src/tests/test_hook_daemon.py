"""Tests for cyt hook daemon lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cyt.hook import daemon as hook_daemon


@pytest.fixture
def pidfile_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "hook-daemon.json"
    monkeypatch.setattr(hook_daemon, "HOOK_DAEMON_PIDFILE", path)
    monkeypatch.setattr("cyt.hook.port.HOOK_DAEMON_PIDFILE", path)
    return path


def test_daemon_start_reuses_existing_server(pidfile_path: Path) -> None:
    with patch("cyt.hook.daemon._find_reusable_hook_port", return_value=8834):
        result = hook_daemon.daemon_start(verbose=False)

    assert result.reused is True
    assert result.port == 8834
    payload = json.loads(pidfile_path.read_text(encoding="utf-8"))
    assert payload["reused"] is True
    assert payload["pid"] is None


def test_spawn_hook_server_passes_resolved_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-token")
    with patch("cyt.hook.daemon.subprocess.Popen") as popen:
        popen.return_value = MagicMock()
        hook_daemon._spawn_hook_server(
            port=8834,
            config_path=None,
            verbose=False,
            extra_env={"OPENROUTER_API_KEY": "or-token"},
        )

    _, kwargs = popen.call_args
    assert kwargs["env"]["OPENROUTER_API_KEY"] == "or-token"
    assert kwargs["env"]["CYT_SKIP_KEYRING"] == "1"


def test_resolve_spawn_credentials_exports_pipeline_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-token")

    def fake_ensure(
        config: dict,
        *,
        credential_sources: dict[str, str],
    ) -> None:
        credential_sources["OPENROUTER_API_KEY"] = "keyring"

    with patch(
        "cyt.hook.daemon.ensure_proxy_pipeline_credentials",
        side_effect=fake_ensure,
    ) as ensure:
        extra = hook_daemon._resolve_spawn_credentials({})

    ensure.assert_called_once()
    assert extra == {"OPENROUTER_API_KEY": "or-token"}


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
    hook_daemon.daemon_stop(verbose=False)
    assert not pidfile_path.exists()


def test_daemon_stop_does_not_kill_reused_external_server(pidfile_path: Path) -> None:
    pidfile_path.write_text(
        json.dumps({"pid": None, "reused": True, "port": 8834}),
        encoding="utf-8",
    )
    with patch("os.kill") as kill:
        hook_daemon.daemon_stop(verbose=True)
        kill.assert_not_called()

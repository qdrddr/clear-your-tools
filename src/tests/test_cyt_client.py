"""Tests for cyt-client hook HTTP forwarding."""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt_client import port as client_port


def test_cyt_client_package_has_no_cyt_imports() -> None:
    for module_name in ("cyt_client.cli", "cyt_client.port", "cyt_client.transport"):
        module = importlib.import_module(module_name)
        assert module.__file__ is not None
        source_path = Path(module.__file__).resolve()
        text = source_path.read_text(encoding="utf-8")
        assert "import cyt." not in text
        assert "from cyt." not in text


def test_is_hook_server_requires_hook_flag() -> None:
    assert client_port.is_hook_server({"name": "cyt", "status": "ok", "hook": True})
    assert not client_port.is_hook_server({"name": "cyt", "status": "ok"})
    assert not client_port.is_hook_server(None)


def test_resolve_hook_url_prefers_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYT_HOOK_URL", "http://127.0.0.1:9999/hook/inject")
    with patch("cyt_client.port._hook_url_is_live", return_value=True):
        assert client_port.resolve_hook_url() == "http://127.0.0.1:9999/hook/inject"


def test_resolve_hook_url_ignores_stale_pidfile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CYT_HOOK_URL", raising=False)
    pidfile = {
        "hook_url": "http://127.0.0.1:8834/hook/inject",
        "port": 8834,
    }
    with patch("cyt_client.port.read_hook_daemon_pidfile", return_value=pidfile):
        with patch("cyt_client.port._hook_url_is_live", return_value=False):
            with patch("cyt_client.port.find_hook_server_port", return_value=None):
                assert client_port.resolve_hook_url() is None


def test_cli_falls_back_on_http_error(capsys: pytest.CaptureFixture[str]) -> None:
    payload = b'{"hook_event_name":"UserPromptSubmit","prompt":"hello"}'
    with patch("cyt_client.cli.resolve_hook_url", return_value="http://127.0.0.1:8834/hook/inject"):
        with patch("cyt_client.cli.post_hook_inject", return_value=(502, b"bad gateway")):
            with patch("cyt_client.cli._fallback_stdin_hook", return_value=0) as fallback:
                from cyt_client.cli import main

                with patch("sys.stdin.buffer.read", return_value=payload):
                    with pytest.raises(SystemExit) as exc:
                        main()
                    assert exc.value.code == 0
                fallback.assert_called_once_with(payload)

    captured = capsys.readouterr()
    assert "falling back" in captured.err


def test_cli_writes_response_body_to_stdout_only(capsys: pytest.CaptureFixture[str]) -> None:
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    with patch("cyt_client.cli.resolve_hook_url", return_value="http://127.0.0.1:8834/hook/inject"):
        with patch(
            "cyt_client.cli.post_hook_inject",
            return_value=(200, b'{"hookSpecificOutput":{"additionalContext":"x"}}'),
        ):
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                main()

    captured = capsys.readouterr()
    assert captured.out == '{"hookSpecificOutput":{"additionalContext":"x"}}'
    assert captured.err == ""


def test_cli_falls_back_when_server_unavailable(capsys: pytest.CaptureFixture[str]) -> None:
    payload = b'{"hook_event_name":"UserPromptSubmit","prompt":"hello"}'
    with patch("cyt_client.cli.resolve_hook_url", return_value=None):
        with patch("cyt_client.cli._fallback_stdin_hook", return_value=0) as fallback:
            from cyt_client.cli import main

            with patch("sys.stdin.buffer.read", return_value=payload):
                with pytest.raises(SystemExit) as exc:
                    main()
                assert exc.value.code == 0
            fallback.assert_called_once_with(payload)

    captured = capsys.readouterr()
    assert "falling back" in captured.err


def test_format_hook_stdout_roundtrip() -> None:
    from cyt.skills.cli import format_hook_stdout

    payload = {"hook_event_name": "UserPromptSubmit"}
    formatted = format_hook_stdout("hello", payload)
    data = json.loads(formatted)
    assert data["hookSpecificOutput"]["additionalContext"] == "hello"

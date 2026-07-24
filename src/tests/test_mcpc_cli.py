"""Tests for mcpc CLI wrapper."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from cyt.mcpc.cli import (
    clear_session_capabilities_cache,
    mcpc_available,
    restart_mcpc_session,
    run_mcpc,
    run_mcpc_json,
    session_supports_capability,
)


def test_run_mcpc_json_parses_stdout() -> None:
    payload = {"sessions": [{"name": "@ctx7", "status": "live"}]}
    with patch(
        "cyt.mcpc.cli.run_mcpc",
        return_value=(0, json.dumps(payload), ""),
    ):
        assert run_mcpc_json("mcpc", []) == payload


def test_run_mcpc_json_returns_none_on_nonzero_exit() -> None:
    with patch("cyt.mcpc.cli.run_mcpc", return_value=(2, "", "tool failed")):
        assert run_mcpc_json("mcpc", ["@ctx7", "tools-list"]) is None


def test_run_mcpc_json_retries_after_not_connected() -> None:
    calls: list[list[str]] = []

    def fake_run_mcpc(_executable: str, args: list[str], **_kwargs: object) -> tuple[int, str, str]:
        calls.append(list(args))
        if len(calls) == 1:
            return 2, "", "Not connected"
        if "restart" in args:
            return 0, "{}", ""
        return 0, "[]", ""

    with patch("cyt.mcpc.cli.run_mcpc", side_effect=fake_run_mcpc):
        assert run_mcpc_json("mcpc", ["@context-mode", "tools-list"]) == []

    assert calls[0] == ["--json", "@context-mode", "tools-list"]
    assert calls[1] == ["--json", "@context-mode", "restart"]
    assert calls[2] == ["--json", "@context-mode", "tools-list"]


def test_run_mcpc_json_retries_when_session_status_not_live() -> None:
    calls: list[list[str]] = []

    def fake_run_mcpc(_executable: str, args: list[str], **_kwargs: object) -> tuple[int, str, str]:
        calls.append(list(args))
        if args == ["--json", "@context-mode", "tools-list"] and len(calls) == 1:
            return 2, "", "tool failed"
        if args == ["--json", "@context-mode"]:
            return 0, json.dumps({"name": "@context-mode", "status": "disconnected"}), ""
        if "restart" in args:
            return 0, "{}", ""
        if args == ["--json", "@context-mode", "tools-list"]:
            return 0, "[]", ""
        return 2, "", "unexpected"

    with patch("cyt.mcpc.cli.run_mcpc", side_effect=fake_run_mcpc):
        assert run_mcpc_json("mcpc", ["@context-mode", "tools-list"]) == []

    assert calls[0] == ["--json", "@context-mode", "tools-list"]
    assert calls[1] == ["--json", "@context-mode"]
    assert calls[2] == ["--json", "@context-mode", "restart"]
    assert calls[3] == ["--json", "@context-mode", "tools-list"]


def test_run_mcpc_json_skips_retry_when_session_status_live() -> None:
    calls: list[list[str]] = []

    def fake_run_mcpc(_executable: str, args: list[str], **_kwargs: object) -> tuple[int, str, str]:
        calls.append(list(args))
        if args == ["--json", "@codebase-memory", "skills-list"]:
            return 2, "", "Method not found"
        if args == ["--json", "@codebase-memory"]:
            return 0, json.dumps({"name": "@codebase-memory", "status": "live"}), ""
        return 0, "{}", ""

    with patch("cyt.mcpc.cli.run_mcpc", side_effect=fake_run_mcpc):
        assert run_mcpc_json("mcpc", ["@codebase-memory", "skills-list"]) is None

    assert calls == [
        ["--json", "@codebase-memory", "skills-list"],
        ["--json", "@codebase-memory"],
    ]
    assert "restart" not in " ".join(" ".join(call) for call in calls)


def test_run_mcpc_json_optional_method_skips_method_not_found_without_warning() -> None:
    stderr = StringIO()
    with (
        patch(
            "cyt.mcpc.cli.run_mcpc",
            return_value=(2, "", "MCP error -32601: Method not found"),
        ),
        patch("cyt.mcpc.cli.logger") as logger,
    ):
        logger.warning.side_effect = lambda msg, *args: stderr.write(str(msg) % args)
        assert (
            run_mcpc_json(
                "mcpc",
                ["@codebase-memory", "skills-list"],
                optional_method=True,
            )
            is None
        )
    assert stderr.getvalue() == ""


def test_session_supports_capability_uses_session_payload() -> None:
    clear_session_capabilities_cache()
    payload = {
        "name": "@hedl",
        "capabilities": {"resources": {}, "tools": {}},
    }
    with patch("cyt.mcpc.cli.run_mcpc_json", return_value=payload):
        assert session_supports_capability("mcpc", "@hedl", "resources") is True
        assert session_supports_capability("mcpc", "@hedl", "skills") is False


def test_run_mcpc_json_quiet_suppresses_warning_logs() -> None:
    stderr = StringIO()
    with (
        patch("cyt.mcpc.cli.run_mcpc", return_value=(2, "", "Not connected")),
        patch("cyt.mcpc.cli.restart_mcpc_session", return_value=False),
        patch("cyt.mcpc.cli.logger") as logger,
    ):
        logger.warning.side_effect = lambda msg, *args: stderr.write(str(msg) % args)
        assert run_mcpc_json("mcpc", ["@context-mode", "tools-list"], quiet=True) is None
    assert stderr.getvalue() == ""


def test_restart_mcpc_session_invokes_restart_command() -> None:
    with patch("cyt.mcpc.cli.run_mcpc", return_value=(0, "{}", "")) as run:
        assert restart_mcpc_session("mcpc", "context-mode") is True
    run.assert_called_once_with(
        "mcpc",
        ["--json", "@context-mode", "restart"],
        timeout=60.0,
        quiet=False,
    )


def test_mcpc_available_uses_which() -> None:
    with patch("cyt.mcpc.cli.shutil.which", return_value="/usr/local/bin/mcpc"):
        assert mcpc_available("mcpc") is True
    with patch("cyt.mcpc.cli.shutil.which", return_value=None):
        assert mcpc_available("missing-mcpc") is False


def test_run_mcpc_missing_executable() -> None:
    with patch("cyt.mcpc.cli.subprocess.run", side_effect=FileNotFoundError):
        code, _stdout, stderr = run_mcpc("missing-mcpc", ["--json"])
    assert code == 127
    assert "not found" in stderr

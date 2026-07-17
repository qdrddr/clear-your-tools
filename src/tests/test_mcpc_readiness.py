"""Tests for MCPC hook readiness probing."""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest

from cyt.mcpc.readiness import (
    MCPC_EMPTY_SESSIONS_HINT,
    MCPC_INSTALL_HINT,
    mcpc_hook_catalog_usable,
    probe_mcpc_sessions,
    report_mcpc_hook_readiness,
)

_MCP_CONFIG = {
    "pruning": {
        "inject_via": "hook",
        "tools": {
            "enabled": True,
            "hook": {
                "tools_from": "mcpc",
                "mcpc": {"executable": "mcpc"},
            },
        },
    },
}


def test_probe_mcpc_sessions_returns_none_when_not_mcpc_mode() -> None:
    config = {"pruning": {"inject_via": "hook", "tools": {"hook": {"tools_from": "executor"}}}}
    assert probe_mcpc_sessions(config) is None


def test_probe_mcpc_sessions_unavailable_when_executable_missing() -> None:
    with patch("cyt.mcpc.readiness.mcpc_available", return_value=False):
        assert probe_mcpc_sessions(_MCP_CONFIG) == "unavailable"


def test_probe_mcpc_sessions_empty_when_no_sessions() -> None:
    payload: dict[str, list[Any]] = {"sessions": []}
    with (
        patch("cyt.mcpc.readiness.mcpc_available", return_value=True),
        patch("cyt.mcpc.readiness.run_mcpc_json", return_value=payload),
    ):
        assert probe_mcpc_sessions(_MCP_CONFIG) == "empty"


def test_probe_mcpc_sessions_ok_when_sessions_present() -> None:
    payload = {"sessions": [{"name": "@cn7", "status": "live"}]}
    with (
        patch("cyt.mcpc.readiness.mcpc_available", return_value=True),
        patch("cyt.mcpc.readiness.run_mcpc_json", return_value=payload),
    ):
        assert probe_mcpc_sessions(_MCP_CONFIG) == "ok"


def test_probe_mcpc_sessions_unavailable_on_invalid_payload() -> None:
    with (
        patch("cyt.mcpc.readiness.mcpc_available", return_value=True),
        patch("cyt.mcpc.readiness.run_mcpc_json", return_value=None),
    ):
        assert probe_mcpc_sessions(_MCP_CONFIG) == "unavailable"


def test_mcpc_hook_catalog_usable() -> None:
    with patch("cyt.mcpc.readiness.probe_mcpc_sessions", return_value="ok"):
        assert mcpc_hook_catalog_usable(_MCP_CONFIG) is True
    with patch("cyt.mcpc.readiness.probe_mcpc_sessions", return_value="empty"):
        assert mcpc_hook_catalog_usable(_MCP_CONFIG) is False
    with patch("cyt.mcpc.readiness.probe_mcpc_sessions", return_value="unavailable"):
        assert mcpc_hook_catalog_usable(_MCP_CONFIG) is False


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        ("unavailable", MCPC_INSTALL_HINT),
        ("empty", MCPC_EMPTY_SESSIONS_HINT),
    ],
)
def test_report_mcpc_hook_readiness_prints_hints(probe: str, expected: str) -> None:
    stderr = StringIO()
    with (
        patch("cyt.mcpc.readiness.probe_mcpc_sessions", return_value=probe),
        patch("cyt.mcpc.readiness.sys.stderr", stderr),
    ):
        report_mcpc_hook_readiness(_MCP_CONFIG)
    assert expected in stderr.getvalue()


def test_report_mcpc_hook_readiness_silent_when_ok() -> None:
    stderr = StringIO()
    with (
        patch("cyt.mcpc.readiness.probe_mcpc_sessions", return_value="ok"),
        patch("cyt.mcpc.readiness.sys.stderr", stderr),
    ):
        report_mcpc_hook_readiness(_MCP_CONFIG)
    assert stderr.getvalue() == ""


def test_report_mcpc_hook_readiness_silent_when_unattended() -> None:
    stderr = StringIO()
    with (
        patch("cyt.mcpc.readiness.probe_mcpc_sessions", return_value="unavailable"),
        patch("cyt.mcpc.readiness.sys.stderr", stderr),
    ):
        report_mcpc_hook_readiness(_MCP_CONFIG, unattended=True)
    assert stderr.getvalue() == ""

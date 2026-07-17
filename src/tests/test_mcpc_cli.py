"""Tests for mcpc CLI wrapper."""

from __future__ import annotations

import json
from unittest.mock import patch

from cyt.mcpc.cli import mcpc_available, run_mcpc, run_mcpc_json


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

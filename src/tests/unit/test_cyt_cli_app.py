"""Smoke tests for the unified dev CLI entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
APP = REPO / "src" / "cyt" / "cli" / "app.py"
PROXY_SHIM = REPO / "src" / "cyt" / "proxy" / "cli.py"
ENV = {**dict(__import__("os").environ), "PYTHONPATH": str(REPO / "src")}


def _run_cli(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env=ENV,
        check=False,
    )


def test_app_cli_tiers_stats_requires_project(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("tools:\n  tiers:\n    mode: off\n", encoding="utf-8")
    env = {**ENV, "CYT_CONFIG": str(config_path)}
    result = subprocess.run(
        [sys.executable, str(APP), "tiers", "stats"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 2
    assert "no project resolved" in (result.stderr or result.stdout)


def test_app_cli_config_current() -> None:
    result = _run_cli(APP, "config", "current")
    assert result.returncode == 0, result.stderr or result.stdout
    assert "schema_version:" in result.stdout or "(missing)" in result.stdout


def test_app_cli_hook_daemon_status() -> None:
    result = _run_cli(APP, "hook", "daemon", "status")
    assert result.returncode == 0, result.stderr or result.stdout


def test_app_cli_help_lists_tiers_and_config() -> None:
    result = _run_cli(APP, "--help")
    assert result.returncode == 0, result.stderr
    assert "tiers" in result.stdout
    assert "config" in result.stdout


def test_proxy_cli_shim_delegates_like_app() -> None:
    app_result = _run_cli(APP, "config", "history")
    shim_result = _run_cli(PROXY_SHIM, "config", "history")
    assert app_result.returncode == 0, app_result.stderr
    assert shim_result.returncode == 0, shim_result.stderr
    assert "head:" in app_result.stdout
    assert shim_result.stdout == app_result.stdout

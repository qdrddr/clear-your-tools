"""Tests for permissions CLI router shortcuts."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_proxy_cli_permissions_shortcut_skips_heavy_cli_impl() -> None:
    cmd = [
        sys.executable,
        str(REPO / "src" / "cyt" / "proxy" / "cli.py"),
        "permissions",
        "mcp",
        "servers",
        "list",
        "--json",
    ]
    env = {**dict(__import__("os").environ), "PYTHONPATH": str(REPO / "src")}
    started = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    elapsed = time.perf_counter() - started
    assert result.returncode == 0, result.stderr
    assert '"enabled"' in result.stdout
    assert elapsed < 5.0

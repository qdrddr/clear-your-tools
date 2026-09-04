"""Tests for permissions CLI router shortcuts."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_proxy_cli_permissions_show_and_skills_list_do_not_fail(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "skills:",
                "  directories:",
                "    - ~/.codex/skills",
                "  permissions:",
                "    deny:",
                "      - path: ~/.codex/skills/.system",
            ],
        ),
        encoding="utf-8",
    )
    env = {**dict(__import__("os").environ), "PYTHONPATH": str(REPO / "src")}
    for subcommand in (
        ["permissions", "show", "--config", str(config_path)],
        ["permissions", "skills", "list", "--config", str(config_path)],
    ):
        cmd = [sys.executable, str(REPO / "src" / "cyt" / "proxy" / "cli.py"), *subcommand]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
        assert result.returncode == 0, result.stderr or result.stdout


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

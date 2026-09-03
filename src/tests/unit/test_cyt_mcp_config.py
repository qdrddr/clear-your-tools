"""Tests for cyt-mcp aggregator config loading."""

from __future__ import annotations

import json
from pathlib import Path

from cyt_mcp.config import expand_mcp_value, is_mcp_server_enabled, load_mcp_servers


def test_expand_mcp_value_user_home_and_workspace(tmp_path: Path) -> None:
    home = Path.home()
    value = expand_mcp_value(
        "${userHome}\\AppData\\Local\\fff-mcp\\bin\\fff-mcp.exe ${workspaceFolder}",
        workspace_folder=tmp_path,
    )
    assert str(home) in value
    assert str(tmp_path.resolve()) in value


def test_load_mcp_servers_expands_cursor_variables(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fff": {
                        "command": "${userHome}\\AppData\\Local\\fff-mcp\\bin\\fff-mcp.exe",
                        "args": ["${workspaceFolder}"],
                    },
                },
            },
        ),
        encoding="utf-8",
    )

    servers = load_mcp_servers(path, workspace_folder=tmp_path)
    command = servers["fff"]["command"]
    assert "${userHome}" not in command
    assert str(Path.home()) in command
    assert servers["fff"]["args"] == [str(tmp_path.resolve())]


def test_is_mcp_server_enabled_defaults_to_true() -> None:
    assert is_mcp_server_enabled({"command": "echo"}) is True
    assert is_mcp_server_enabled({"command": "echo", "enabled": True}) is True


def test_is_mcp_server_enabled_respects_explicit_false() -> None:
    assert is_mcp_server_enabled({"command": "echo", "enabled": False}) is False
    assert is_mcp_server_enabled({"command": "echo", "enabled": "false"}) is False


def test_load_mcp_servers_skips_disabled_servers(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "active": {"command": "echo", "args": ["ok"]},
                    "disabled": {"command": "echo", "args": ["no"], "enabled": False},
                },
            },
        ),
        encoding="utf-8",
    )

    servers = load_mcp_servers(path)

    assert set(servers) == {"active"}
    assert "enabled" not in servers["active"]


def test_load_mcp_servers_keeps_disabled_entries_in_file_but_not_runtime(
    tmp_path: Path,
) -> None:
    """Migration preserves enabled:false in JSON; load_mcp_servers omits at runtime."""
    path = tmp_path / "cursor.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fff": {
                        "command": "fff-mcp",
                        "enabled": False,
                    },
                },
            },
        ),
        encoding="utf-8",
    )

    assert json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["fff"]["enabled"] is False
    assert load_mcp_servers(path) == {}

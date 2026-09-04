"""Tests for cyt-mcp aggregator config loading."""

from __future__ import annotations

import json
from pathlib import Path

from cyt_mcp.config import expand_mcp_value, is_mcp_server_enabled, load_mcp_servers


def test_load_mcp_servers_filters_deny_entries(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "allowed": {"command": "echo"},
                    "blocked": {"command": "echo"},
                },
            },
        ),
        encoding="utf-8",
    )
    servers = load_mcp_servers(path, deny_entries=("blocked",))
    assert set(servers) == {"allowed"}


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


def test_load_mcp_servers_ignores_enabled_key_without_deny(tmp_path: Path) -> None:
    """Runtime policy uses config.yaml deny only; enabled:false in JSON is not authoritative."""
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "disabled-in-json": {"command": "echo", "enabled": False},
                    "active": {"command": "echo"},
                },
            },
        ),
        encoding="utf-8",
    )
    servers = load_mcp_servers(path)
    assert set(servers) == {"disabled-in-json", "active"}


def test_load_mcp_servers_enabled_false_filtered_when_in_deny(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "blocked": {"command": "echo", "enabled": False},
                    "active": {"command": "echo"},
                },
            },
        ),
        encoding="utf-8",
    )
    servers = load_mcp_servers(path, deny_entries=("blocked",))
    assert set(servers) == {"active"}

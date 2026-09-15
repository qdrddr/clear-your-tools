"""Tests for cyt-mcp aggregator config loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt_mcp.config import (
    expand_mcp_value,
    is_mcp_server_enabled,
    listed_mcp_server_names,
    load_aggregator_config,
    load_mcp_servers,
)


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


def test_listed_mcp_server_names_includes_disabled_entries(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fff": {"command": "echo", "enabled": False},
                    "active": {"command": "echo"},
                },
            },
        ),
        encoding="utf-8",
    )
    assert listed_mcp_server_names(path) == frozenset({"fff", "active"})


def test_unified_load_workspace_overrides_user_on_name_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_mcp_dir = tmp_path / "user-mcp"
    user_mcp_dir.mkdir()
    user_defs = user_mcp_dir / "cursor.json"
    user_defs.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fff": {"command": "echo", "args": ["user"]},
                    "global-only": {"command": "echo"},
                },
            },
        ),
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    ws_defs = tmp_path / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    ws_defs.parent.mkdir(parents=True)
    ws_defs.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fff": {"command": "echo", "args": ["workspace"]},
                    "workspace-only": {"command": "echo"},
                },
            },
        ),
        encoding="utf-8",
    )
    agg = tmp_path / ".agents" / "cyt" / "config" / "mcp-config.yaml"
    agg.parent.mkdir(parents=True, exist_ok=True)
    agg.write_text("agent: cursor\ncatalog_scope: workspace\n", encoding="utf-8")

    monkeypatch.setattr("cyt_mcp.config.DEFAULT_MCP_DIR", user_mcp_dir)
    monkeypatch.chdir(tmp_path)
    config = load_aggregator_config(agent="cursor", aggregator_path=agg, workspace_folder=tmp_path)

    assert config.catalog_scope == "workspace"
    assert set(config.mcp_servers) == {"fff", "global-only", "workspace-only"}
    assert config.mcp_servers["fff"]["args"] == ["workspace"]
    assert config.server_origins["fff"] == "workspace"
    assert config.server_origins["global-only"] == "user"
    assert config.server_origins["workspace-only"] == "workspace"


def test_unified_load_user_scope_merges_workspace_from_project_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_mcp_dir = tmp_path / "user-mcp"
    user_mcp_dir.mkdir()
    user_defs = user_mcp_dir / "cursor.json"
    user_defs.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fff": {"command": "echo", "args": ["user"]},
                    "global-only": {"command": "echo"},
                },
            },
        ),
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    ws_defs = tmp_path / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    ws_defs.parent.mkdir(parents=True)
    ws_defs.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fff": {"command": "echo", "args": ["workspace"]},
                    "workspace-only": {"command": "echo"},
                },
            },
        ),
        encoding="utf-8",
    )
    agg = tmp_path / "mcp-config.yaml"
    agg.write_text("agent: cursor\n", encoding="utf-8")

    monkeypatch.setattr("cyt_mcp.config.DEFAULT_MCP_DIR", user_mcp_dir)
    monkeypatch.chdir(tmp_path)
    config = load_aggregator_config(agent="cursor", aggregator_path=agg)

    assert config.catalog_scope == "user"
    assert config.workspace_root == tmp_path.resolve()
    assert set(config.mcp_servers) == {"fff", "global-only", "workspace-only"}
    assert config.mcp_servers["fff"]["args"] == ["workspace"]
    assert config.server_origins["fff"] == "workspace"
    assert config.server_origins["global-only"] == "user"
    assert config.server_origins["workspace-only"] == "workspace"


def test_unified_load_user_only_without_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_mcp_dir = tmp_path / "user-mcp"
    user_mcp_dir.mkdir()
    user_defs = user_mcp_dir / "cursor.json"
    user_defs.write_text(
        json.dumps({"mcpServers": {"fff": {"command": "echo"}}}),
        encoding="utf-8",
    )
    agg = tmp_path / "mcp-config.yaml"
    agg.write_text("agent: cursor\n", encoding="utf-8")

    monkeypatch.setattr("cyt_mcp.config.DEFAULT_MCP_DIR", user_mcp_dir)
    monkeypatch.chdir(tmp_path)
    config = load_aggregator_config(agent="cursor", aggregator_path=agg)

    assert config.catalog_scope == "user"
    assert config.workspace_root is None
    assert set(config.mcp_servers) == {"fff"}
    assert config.server_origins == {"fff": "user"}


def test_load_aggregator_config_expands_workspace_folder_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_mcp_dir = tmp_path / "user-mcp"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "cursor.json").write_text(
        json.dumps({"mcpServers": {"context7": {"url": "https://example.com/mcp"}}}),
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    ws_defs = tmp_path / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    ws_defs.parent.mkdir(parents=True)
    ws_defs.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    agg = tmp_path / ".agents" / "cyt" / "config" / "mcp-config.yaml"
    agg.write_text(
        "default_agent: cursor\ncatalog_scope: workspace\nagents:\n  cursor: mcp/cursor.json\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("cyt_mcp.config.DEFAULT_MCP_DIR", user_mcp_dir)
    monkeypatch.chdir(tmp_path)
    config = load_aggregator_config(
        agent="cursor",
        aggregator_path=Path("${workspaceFolder}/.agents/cyt/config/mcp-config.yaml"),
        workspace_folder=tmp_path,
    )

    assert config.aggregator_path == agg
    assert set(config.mcp_servers) == {"context7"}


def test_unified_load_merges_user_and_workspace_defs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_mcp_dir = tmp_path / "user-mcp"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "cursor.json").write_text(
        json.dumps({"mcpServers": {"user-only": {"command": "echo"}}}),
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    ws_defs = tmp_path / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    ws_defs.parent.mkdir(parents=True)
    ws_defs.write_text(
        json.dumps({"mcpServers": {"workspace-only": {"command": "echo"}}}),
        encoding="utf-8",
    )
    agg = tmp_path / ".agents" / "cyt" / "config" / "mcp-config.yaml"
    agg.write_text("agent: cursor\ncatalog_scope: workspace\n", encoding="utf-8")

    monkeypatch.setattr("cyt_mcp.config.DEFAULT_MCP_DIR", user_mcp_dir)
    monkeypatch.chdir(tmp_path)
    config = load_aggregator_config(agent="cursor", aggregator_path=agg, workspace_folder=tmp_path)

    assert set(config.mcp_servers) == {"user-only", "workspace-only"}


def test_workspace_load_uses_canonical_defs_when_aggregator_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_mcp_dir = tmp_path / "user-mcp"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "cursor.json").write_text(
        json.dumps({"mcpServers": {"global-only": {"command": "echo"}}}),
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    ws_defs = tmp_path / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    ws_defs.parent.mkdir(parents=True)
    ws_defs.write_text(
        json.dumps({"mcpServers": {"workspace-only": {"command": "echo"}}}),
        encoding="utf-8",
    )
    agg = tmp_path / ".agents" / "cyt" / "config" / "mcp-config.yaml"

    monkeypatch.setattr("cyt_mcp.config.DEFAULT_MCP_DIR", user_mcp_dir)
    monkeypatch.chdir(tmp_path)
    config = load_aggregator_config(
        agent="cursor",
        aggregator_path=agg,
        workspace_folder=tmp_path,
    )

    assert config.catalog_scope == "workspace"
    assert config.agent_mcp_path == ws_defs.resolve()
    assert set(config.mcp_servers) == {"global-only", "workspace-only"}


def test_workspace_load_rejects_global_agent_path_in_aggregator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_mcp_dir = tmp_path / "user-mcp"
    user_mcp_dir.mkdir()
    global_defs = user_mcp_dir / "cursor.json"
    global_defs.write_text(
        json.dumps({"mcpServers": {"global-only": {"command": "echo"}}}),
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    ws_defs = tmp_path / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    ws_defs.parent.mkdir(parents=True)
    ws_defs.write_text(
        json.dumps({"mcpServers": {"workspace-only": {"command": "echo"}}}),
        encoding="utf-8",
    )
    agg = tmp_path / ".agents" / "cyt" / "config" / "mcp-config.yaml"
    agg.parent.mkdir(parents=True, exist_ok=True)
    agg.write_text(
        "\n".join(
            [
                "agent: cursor",
                "catalog_scope: workspace",
                "agents:",
                f"  cursor: {global_defs.as_posix()}",
            ],
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("cyt_mcp.config.DEFAULT_MCP_DIR", user_mcp_dir)
    monkeypatch.chdir(tmp_path)
    config = load_aggregator_config(
        agent="cursor",
        aggregator_path=agg,
        workspace_folder=tmp_path,
    )

    assert config.agent_mcp_path == ws_defs.resolve()
    assert set(config.mcp_servers) == {"global-only", "workspace-only"}


def test_workspace_load_resolves_relative_agent_path_from_aggregator_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_mcp_dir = tmp_path / "user-mcp"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "cursor.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    (tmp_path / ".git").mkdir()
    ws_defs = tmp_path / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    ws_defs.parent.mkdir(parents=True)
    ws_defs.write_text(
        json.dumps({"mcpServers": {"relative-only": {"command": "echo"}}}),
        encoding="utf-8",
    )
    agg = tmp_path / ".agents" / "cyt" / "config" / "mcp-config.yaml"
    agg.write_text(
        "\n".join(
            [
                "default_agent: cursor",
                "catalog_scope: workspace",
                "agents:",
                "  cursor: mcp/cursor.json",
            ],
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("cyt_mcp.config.DEFAULT_MCP_DIR", user_mcp_dir)
    monkeypatch.chdir(tmp_path)
    config = load_aggregator_config(
        agent="cursor",
        aggregator_path=agg,
        workspace_folder=tmp_path,
    )

    assert config.agent_mcp_path == ws_defs.resolve()
    assert set(config.mcp_servers) == {"relative-only"}


def test_unified_load_user_deny_does_not_apply_to_workspace_servers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_mcp_dir = tmp_path / "user-mcp"
    user_mcp_dir.mkdir()
    (user_mcp_dir / "cursor.json").write_text(
        json.dumps({"mcpServers": {"global-only": {"command": "echo"}}}),
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    ws_config_dir = tmp_path / ".agents" / "cyt" / "config"
    ws_config_dir.mkdir(parents=True)
    (ws_config_dir / "config.yaml").write_text(
        "mcp:\n  permissions:\n    deny:\n      - fff\n",
        encoding="utf-8",
    )
    ws_defs = ws_config_dir / "mcp" / "cursor.json"
    ws_defs.parent.mkdir(parents=True, exist_ok=True)
    ws_defs.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fff": {"command": "echo"},
                    "workspace-only": {"command": "echo"},
                },
            },
        ),
        encoding="utf-8",
    )
    agg = ws_config_dir / "mcp-config.yaml"
    agg.write_text("agent: cursor\ncatalog_scope: workspace\n", encoding="utf-8")

    monkeypatch.setattr("cyt_mcp.config.DEFAULT_MCP_DIR", user_mcp_dir)
    monkeypatch.chdir(tmp_path)
    config = load_aggregator_config(agent="cursor", aggregator_path=agg, workspace_folder=tmp_path)

    assert set(config.mcp_servers) == {"global-only", "workspace-only"}

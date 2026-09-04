"""Tests for MCP defs JSON sync with permissions policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cyt.permissions.editor import disable_mcp_server
from cyt.permissions.mcp_defs import disabled_server_names, set_mcp_server_enabled_flag
from cyt.tools import cyt_mcp_setup


def test_disabled_server_names_reads_enabled_flag() -> None:
    servers = {
        "on": {"command": "echo"},
        "off": {"command": "echo", "enabled": False},
    }
    assert disabled_server_names(servers) == ["off"]


def test_migrate_agent_backends_syncs_disabled_to_config_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "mcp.json"
    target_dir = tmp_path / "cyt_mcp"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agents: {}\n", encoding="utf-8")

    source.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "wiseinfotec": {"url": "https://mcp.example.com/mcp"},
                    "legacy-off": {"command": "echo", "enabled": False},
                },
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", source)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", target_dir)

    def _config_path(scope: str, **kwargs: object) -> Path:
        del scope, kwargs
        return config_path

    monkeypatch.setattr("cyt.permissions.paths.permissions_config_path", _config_path)
    monkeypatch.setattr("cyt.permissions.editor.permissions_config_path", _config_path)

    cyt_mcp_setup.migrate_agent_backends("cursor")

    backend_payload = json.loads((target_dir / "cursor.json").read_text(encoding="utf-8"))
    assert backend_payload["mcpServers"]["legacy-off"]["enabled"] is False

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["agents"]["cursor"]["mcp"]["permissions"]["deny"] == ["legacy-off"]


def test_disable_mcp_server_updates_json_enabled_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agents: {}\n", encoding="utf-8")
    defs_path = tmp_path / "cursor.json"
    defs_path.write_text(
        json.dumps({"mcpServers": {"demo": {"command": "echo", "enabled": True}}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "cyt.permissions.mcp_defs.mcp_server_defs_path",
        lambda **kwargs: defs_path,
    )

    disable_mcp_server(
        "demo",
        scope="global",
        agent_target="cursor",
        agent="cursor",
        global_config_path=config_path,
    )

    payload = json.loads(defs_path.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["demo"]["enabled"] is False


def test_set_mcp_server_enabled_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defs_path = tmp_path / "cursor.json"
    defs_path.write_text(
        json.dumps({"mcpServers": {"demo": {"command": "echo"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyt.permissions.mcp_defs.mcp_server_defs_path",
        lambda **kwargs: defs_path,
    )

    assert set_mcp_server_enabled_flag("demo", False, agent="cursor", scope="global") is True
    payload = json.loads(defs_path.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["demo"]["enabled"] is False

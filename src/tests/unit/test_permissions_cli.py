"""Tests for ``cyt permissions`` CLI edits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import yaml

from cyt.permissions.cli import run_permissions_export, run_permissions_show
from cyt.permissions.editor import disable_mcp_server, disable_skill, enable_mcp_server
from cyt.permissions.paths import permissions_config_path


def _args(**kwargs: object) -> argparse.Namespace:
    defaults = {
        "scope": "effective",
        "agent": "all",
        "json": False,
        "config": None,
        "workspace": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_disable_and_enable_mcp_server_writes_overlay(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agents: {}\n", encoding="utf-8")

    disable_mcp_server(
        "demo-server",
        scope="global",
        agent_target="cursor",
        agent="cursor",
        global_config_path=config_path,
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["agents"]["cursor"]["mcp"]["permissions"]["deny"] == ["demo-server"]

    enable_mcp_server(
        "demo-server",
        scope="global",
        agent_target="cursor",
        agent="cursor",
        global_config_path=config_path,
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["agents"]["cursor"]["mcp"]["permissions"]["deny"] == []


def test_permissions_export_writes_claude_json(tmp_path: Path) -> None:
    output_path = tmp_path / "settings.json"
    output_path.write_text('{"other": true}\n', encoding="utf-8")
    run_permissions_export(
        _args(
            format="claude",
            output=output_path,
            agent="cursor",
            scope="effective",
        ),
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["other"] is True
    assert "permissions" in payload
    assert isinstance(payload["permissions"]["deny"], list)


def test_enable_mcp_server_shows_upstream_deny_notice(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    global_path = tmp_path / "global.yaml"
    global_path.write_text(
        "\n".join(
            [
                "agents:",
                "  cursor:",
                "    mcp:",
                "      permissions:",
                "        deny: [demo-server]",
            ],
        ),
        encoding="utf-8",
    )
    workspace_path = tmp_path / ".cursor" / "cyt" / "config" / "config.yaml"
    workspace_path.parent.mkdir(parents=True)
    workspace_path.write_text("agents: {}\n", encoding="utf-8")

    from cyt.permissions.cli import _mcp_servers_handler

    handler = _mcp_servers_handler("enable")
    handler(
        argparse.Namespace(
            server="demo-server",
            scope="workspace",
            agent="cursor",
            json=False,
            config=global_path,
            workspace=tmp_path,
        ),
    )
    captured = capsys.readouterr()
    assert "still disabled by policy in another scope" in captured.err


def test_disable_skill_all_agents_uses_shared_workspace_path(tmp_path: Path) -> None:
    disable_skill(
        "upgrade-guide",
        scope="workspace",
        agent_target="all",
        agent="all",
        workspace_root=tmp_path,
    )
    expected = permissions_config_path("workspace", agent="all", workspace_root=tmp_path)
    assert expected.is_file()
    raw = yaml.safe_load(expected.read_text(encoding="utf-8"))
    assert raw["skills"]["permissions"]["deny"] == ["upgrade-guide"]


def test_permissions_show_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_permissions_show(
        _args(
            json=True,
            scope="effective",
            agent="cursor",
        ),
    )
    captured = capsys.readouterr()
    assert '"mcp"' in captured.out
    assert '"skills"' in captured.out


def test_permissions_show_defaults_to_all_agent(capsys: pytest.CaptureFixture[str]) -> None:
    run_permissions_show(_args(json=True, scope="effective"))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["agent"] == "all"

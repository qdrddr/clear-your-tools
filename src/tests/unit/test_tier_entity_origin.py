"""Tests for tier entity origin resolution."""

from __future__ import annotations

import json
from pathlib import Path

from cyt.tiers.entity_origin import (
    _mcp_server_line_in_config,
    resolve_mcp_server_origin,
    resolve_skill_scope,
    resolve_tool_origin_fields,
)


def test_resolve_mcp_server_origin_prefers_workspace(tmp_path: Path) -> None:
    global_path = tmp_path / "global" / "cursor.json"
    workspace_path = tmp_path / "workspace" / "cursor.json"
    global_path.parent.mkdir(parents=True)
    workspace_path.parent.mkdir(parents=True)
    global_path.write_text(
        json.dumps({"mcpServers": {"shared-server": {"command": "echo"}}}),
        encoding="utf-8",
    )
    workspace_path.write_text(
        json.dumps({"mcpServers": {"shared-server": {"command": "echo"}}}),
        encoding="utf-8",
    )

    from unittest.mock import patch

    with patch(
        "cyt.tiers.entity_origin._mcp_config_candidates",
        return_value=[
            ("workspace", workspace_path),
            ("user", global_path),
        ],
    ):
        scope, path, line = resolve_mcp_server_origin("shared-server")
    assert scope == "workspace"
    assert path == str(workspace_path.resolve())
    assert line == 1


def test_mcp_server_line_in_config_finds_key_line(tmp_path: Path) -> None:
    config_path = tmp_path / "cursor.json"
    config_path.write_text(
        '{\n  "mcpServers": {\n    "demo-server": {\n      "command": "echo"\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    assert _mcp_server_line_in_config(config_path, "demo-server") == 3


def test_resolve_tool_origin_fields_includes_server_line(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    mcp_defs = repo / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    mcp_defs.parent.mkdir(parents=True)
    mcp_defs.write_text(
        '{\n  "mcpServers": {\n    "codebase-memory": {\n      "command": "echo"\n    }\n  }\n}\n',
        encoding="utf-8",
    )

    fields = resolve_tool_origin_fields(
        "cyt_mcp:codebase-memory_delete_project",
        config={},
        workspace_root=repo,
    )
    assert fields["mcp_server"] == "codebase-memory"
    assert fields["source_path"] == str(mcp_defs.resolve())
    assert fields["source_line"] == 3


def test_resolve_tool_origin_fields_for_mcpc_tool(tmp_path: Path) -> None:
    ws_config = tmp_path / ".agents" / "cyt" / "config" / "mcp-config.yaml"
    ws_config.parent.mkdir(parents=True)
    ws_config.write_text(
        "catalog_scope: workspace\nagents:\n  cursor: mcp/cursor.json\n",
        encoding="utf-8",
    )

    from cyt.hook.install_scope import CytInstallScope

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    scope = CytInstallScope(workspace_root=repo)
    ws_config_at = scope.workspace_all_agents_cyt_mcp_config_path()
    assert ws_config_at is not None
    ws_config_at.parent.mkdir(parents=True, exist_ok=True)
    ws_config_at.write_text(
        "catalog_scope: workspace\nagents:\n  cursor: mcp/cursor.json\n",
        encoding="utf-8",
    )

    fields = resolve_tool_origin_fields(
        "unknown:@fff/grep",
        config={},
        workspace_root=repo,
    )
    assert fields["catalog_source"] == "mcpc"
    assert fields["mcp_server"] == "fff"
    assert fields["scope"] == "workspace"
    assert fields["source_path"] == str(ws_config_at.resolve())


def test_resolve_skill_scope_workspace_vs_user(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    ws_skill = repo / ".cursor" / "skills" / "demo" / "SKILL.md"
    ws_skill.parent.mkdir(parents=True)
    ws_skill.write_text("# demo", encoding="utf-8")
    user_skill = tmp_path / "skills" / "demo" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text("# demo", encoding="utf-8")

    assert resolve_skill_scope(str(ws_skill.resolve()), workspace_root=repo) == "workspace"
    assert resolve_skill_scope(str(user_skill.resolve()), workspace_root=repo) == "user"

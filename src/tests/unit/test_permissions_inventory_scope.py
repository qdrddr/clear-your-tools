"""Tests for permissions inventory scope and agent filtering."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from cyt.permissions.inventory.mcp import (
    list_mcp_servers,
    load_mcp_server_names,
    load_mcp_server_sources,
)
from cyt.permissions.inventory.skills import (
    directory_belongs_to_agent,
    enumerate_skill_names,
)
from cyt.permissions.match import explicit_denied_servers
from cyt.permissions.schema import EffectivePermissions, McpPermissions


def test_explicit_denied_servers() -> None:
    assert explicit_denied_servers(("demo-server", "fff/find_files", "fff/*")) == [
        "demo-server",
        "fff",
    ]


def test_load_mcp_server_names_merges_global_and_workspace(tmp_path: Path) -> None:
    global_path = tmp_path / "global" / "cursor.json"
    workspace_path = tmp_path / "workspace" / "cursor.json"
    global_path.parent.mkdir(parents=True)
    workspace_path.parent.mkdir(parents=True)
    global_path.write_text(
        json.dumps({"mcpServers": {"global-only": {"command": "echo"}}}),
        encoding="utf-8",
    )
    workspace_path.write_text(
        json.dumps({"mcpServers": {"workspace-only": {"command": "echo"}}}),
        encoding="utf-8",
    )

    with patch(
        "cyt.permissions.inventory.mcp.mcp_server_defs_path",
        side_effect=lambda *, agent, scope, workspace_root=None: {
            ("cursor", "global"): global_path,
            ("cursor", "workspace"): workspace_path,
        }[(agent, scope)],
    ):
        assert load_mcp_server_names(agent="cursor", scope="global") == ["global-only"]
        assert load_mcp_server_names(agent="cursor", scope="workspace") == ["workspace-only"]
        assert load_mcp_server_names(agent="cursor", scope="effective") == [
            "global-only",
            "workspace-only",
        ]


def test_load_mcp_server_sources_tracks_config_layer(tmp_path: Path) -> None:
    global_path = tmp_path / "global" / "cursor.json"
    workspace_path = tmp_path / "workspace" / "cursor.json"
    global_path.parent.mkdir(parents=True)
    workspace_path.parent.mkdir(parents=True)
    global_path.write_text(
        json.dumps({"mcpServers": {"shared-server": {}, "global-only": {}}}),
        encoding="utf-8",
    )
    workspace_path.write_text(
        json.dumps({"mcpServers": {"shared-server": {}, "workspace-only": {}}}),
        encoding="utf-8",
    )

    with patch(
        "cyt.permissions.inventory.mcp.mcp_server_defs_path",
        side_effect=lambda *, agent, scope, workspace_root=None: {
            ("cursor", "global"): global_path,
            ("cursor", "workspace"): workspace_path,
        }[(agent, scope)],
    ):
        assert load_mcp_server_sources(agent="cursor", scope="effective") == {
            "global-only": "user",
            "workspace-only": "workspace",
            "shared-server": "workspace",
        }


def test_list_mcp_servers_includes_deny_only_servers() -> None:
    effective = EffectivePermissions(
        mcp=McpPermissions(deny=("missing-server",), allow=()),
    )
    with (
        patch(
            "cyt.permissions.inventory.mcp.load_mcp_server_sources",
            return_value={"visible-server": "workspace"},
        ),
        patch("cyt.permissions.inventory.mcp.effective_permissions", return_value=effective),
    ):
        enabled, disabled = list_mcp_servers(agent="cursor", policy_agent="cursor")
    assert [item.name for item in enabled] == ["visible-server"]
    assert enabled[0].source == "workspace"
    assert [item.name for item in disabled] == ["missing-server"]
    assert disabled[0].source is None


def test_directory_belongs_to_agent() -> None:
    assert directory_belongs_to_agent("~/.cursor/skills", "cursor")
    assert directory_belongs_to_agent(".cursor/skills", "cursor")
    assert not directory_belongs_to_agent(".codex/skills", "cursor")
    assert directory_belongs_to_agent("~/.codex/skills", "codex")


def test_skills_directories_for_agent_uses_per_agent_defaults() -> None:
    from cyt.config import skills_directories_for_agent

    config = {
        "skills": {
            "directories": ["~/.agents/skills"],
        },
        "agents": {
            "cursor": {"skills": {"directories": ["~/.cursor/skills", ".cursor/skills"]}},
        },
    }
    cursor_dirs = skills_directories_for_agent(config, agent="cursor")
    assert "~/.agents/skills" in cursor_dirs[0] or cursor_dirs[0].endswith(".agents/skills")
    assert any(
        ".cursor/skills" in directory or directory.endswith(".cursor/skills")
        for directory in cursor_dirs
    )


def test_skills_directories_for_agent_merges_global_and_agent_paths() -> None:
    from cyt.config import skills_directories_for_agent

    config = {
        "skills": {
            "directories": ["~/.agents/skills"],
        },
        "agents": {
            "cursor": {"skills": {"directories": ["~/.cursor/skills"]}},
            "codex": {"skills": {"directories": ["~/.codex/skills"]}},
            "claude": {"skills": {"directories": []}},
        },
    }
    cursor_dirs = skills_directories_for_agent(config, agent="cursor")
    assert len(cursor_dirs) == 2
    codex_dirs = skills_directories_for_agent(config, agent="codex")
    assert len(codex_dirs) == 2
    all_dirs = skills_directories_for_agent(config, agent="all")
    assert len(all_dirs) == 3


def test_enumerate_skill_names_scopes_to_agent(tmp_path: Path) -> None:
    cursor_dir = tmp_path / ".cursor" / "skills" / "cursor-skill"
    codex_dir = tmp_path / ".codex" / "skills" / "codex-skill"
    cursor_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)
    (cursor_dir / "SKILL.md").write_text("---\nname: cursor-skill\n---\n", encoding="utf-8")
    (codex_dir / "SKILL.md").write_text("---\nname: codex-skill\n---\n", encoding="utf-8")
    config = {
        "skills": {
            "directories": [],
        },
        "agents": {
            "cursor": {"skills": {"directories": [str(cursor_dir.parent)]}},
            "codex": {"skills": {"directories": [str(codex_dir.parent)]}},
            "claude": {"skills": {"directories": []}},
        },
    }
    cursor_names = [name for name, _, _ in enumerate_skill_names(config, agent="cursor")]
    codex_names = [name for name, _, _ in enumerate_skill_names(config, agent="codex")]
    assert cursor_names == ["cursor-skill"]
    assert codex_names == ["codex-skill"]

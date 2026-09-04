"""Tests for permissions union merge."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cyt.permissions.merge import effective_permissions


def test_effective_permissions_loads_global_config_from_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "skills:",
                "  permissions:",
                "    deny:",
                "      - from-file",
                "    allow: []",
            ],
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyt.permissions.merge.DEFAULT_USER_CONFIG_PATH",
        config_path,
    )
    effective = effective_permissions(agent="all")
    assert "from-file" in effective.skills.deny


def test_effective_permissions_unions_deny_across_layers(tmp_path: Path) -> None:
    global_cfg = {
        "mcp": {"permissions": {"deny": ["global-server"], "allow": []}},
        "skills": {"permissions": {"deny": ["global-skill"], "allow": []}},
        "agents": {
            "cursor": {
                "mcp": {"permissions": {"deny": ["agent-server"]}},
                "skills": {"permissions": {"deny": ["agent-skill"]}},
            },
        },
    }
    ws_cfg = {
        "mcp": {"permissions": {"deny": ["workspace-server"]}},
        "skills": {"permissions": {"deny": ["workspace-skill"]}},
    }
    effective = effective_permissions(
        agent="cursor",
        global_config=global_cfg,
        workspace_config=ws_cfg,
        workspace_root=tmp_path,
    )
    assert set(effective.mcp.deny) == {
        "global-server",
        "agent-server",
        "workspace-server",
    }
    assert set(effective.skills.deny) == {
        "global-skill",
        "agent-skill",
        "workspace-skill",
    }


def test_effective_mcp_permissions_and_skills_permissions(tmp_path: Path) -> None:
    from cyt.permissions.merge import effective_mcp_permissions, effective_skills_permissions

    global_cfg = {
        "mcp": {"permissions": {"deny": ["server-a"], "allow": []}},
        "skills": {"permissions": {"deny": ["skill-a"], "allow": []}},
    }
    mcp = effective_mcp_permissions(
        agent="cursor",
        global_config=global_cfg,
        workspace_root=tmp_path,
    )
    skills = effective_skills_permissions(
        agent="cursor",
        global_config=global_cfg,
        workspace_root=tmp_path,
    )
    assert mcp.deny == ("server-a",)
    assert skills.deny == ("skill-a",)


def test_effective_permissions_all_agent_skips_agent_overlays(tmp_path: Path) -> None:
    global_cfg = {
        "mcp": {"permissions": {"deny": ["global-server"], "allow": []}},
        "skills": {"permissions": {"deny": ["global-skill"], "allow": []}},
        "agents": {
            "cursor": {
                "mcp": {"permissions": {"deny": ["agent-server"]}},
                "skills": {"permissions": {"deny": ["agent-skill"]}},
            },
        },
    }
    ws_cfg = {
        "mcp": {"permissions": {"deny": ["workspace-server"]}},
        "skills": {"permissions": {"deny": ["workspace-skill"]}},
    }
    effective = effective_permissions(
        agent="all",
        global_config=global_cfg,
        workspace_config=ws_cfg,
        workspace_root=tmp_path,
    )
    assert set(effective.mcp.deny) == {"global-server", "workspace-server"}
    assert set(effective.skills.deny) == {"global-skill", "workspace-skill"}


def test_effective_permissions_unions_shared_workspace_layer(tmp_path: Path) -> None:
    global_cfg = {
        "mcp": {"permissions": {"deny": ["global-mcp"], "allow": []}},
        "skills": {"permissions": {"deny": ["global-skill"], "allow": []}},
    }
    shared_cfg_path = tmp_path / ".agents" / "cyt" / "config" / "config.yaml"
    shared_cfg_path.parent.mkdir(parents=True)
    shared_cfg_path.write_text(
        "\n".join(
            [
                "skills:",
                "  permissions:",
                "    deny: [shared-skill]",
            ],
        ),
        encoding="utf-8",
    )
    agent_cfg_path = tmp_path / ".cursor" / "cyt" / "config" / "config.yaml"
    agent_cfg_path.parent.mkdir(parents=True)
    agent_cfg_path.write_text(
        "\n".join(
            [
                "skills:",
                "  permissions:",
                "    deny: [cursor-skill]",
            ],
        ),
        encoding="utf-8",
    )
    effective = effective_permissions(
        agent="cursor",
        global_config=global_cfg,
        workspace_root=tmp_path,
    )
    assert set(effective.skills.deny) == {"global-skill", "shared-skill", "cursor-skill"}


def test_permissions_config_path_uses_shared_workspace_for_all(tmp_path: Path) -> None:
    from cyt.permissions.paths import permissions_config_path

    path = permissions_config_path("workspace", agent="all", workspace_root=tmp_path)
    assert path == tmp_path / ".agents" / "cyt" / "config" / "config.yaml"


def test_permissions_config_path_uses_shared_workspace_for_agent(tmp_path: Path) -> None:
    from cyt.permissions.paths import permissions_config_path

    path = permissions_config_path("workspace", agent="cursor", workspace_root=tmp_path)
    assert path == tmp_path / ".agents" / "cyt" / "config" / "config.yaml"


def test_disable_skill_all_agents_writes_shared_workspace_config(tmp_path: Path) -> None:
    from cyt.permissions.editor import disable_skill

    disable_skill(
        "upgrade-guide",
        scope="workspace",
        agent_target="all",
        agent="all",
        workspace_root=tmp_path,
    )
    config_path = tmp_path / ".agents" / "cyt" / "config" / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["skills"]["permissions"]["deny"] == ["upgrade-guide"]


def test_effective_permissions_deduplicates_deny_entries(tmp_path: Path) -> None:
    global_cfg = {
        "mcp": {"permissions": {"deny": ["dup", "dup"], "allow": []}},
    }
    ws_cfg = {"mcp": {"permissions": {"deny": ["dup"]}}}
    effective = effective_permissions(
        agent="cursor",
        global_config=global_cfg,
        workspace_config=ws_cfg,
        workspace_root=tmp_path,
    )
    assert effective.mcp.deny == ("dup",)

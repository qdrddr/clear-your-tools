"""Tests for workspace-aware hook config and catalog registry merge."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.hook.workspace_config import (
    HOOK_WORKSPACE_CONFIG_KEY,
    resolve_hook_request_config,
    set_hook_workspace_in_config,
)
from cyt_mcp.catalog import merge_catalog_payloads


def test_merge_catalog_payloads_workspace_overrides_global() -> None:
    base = {
        "agent": "cursor",
        "tools": [{"name": "a_tool", "input_schema": {}}],
        "degraded_servers": ["global-down"],
    }
    overlay = {
        "agent": "cursor",
        "tools": [{"name": "a_tool", "input_schema": {"type": "object", "properties": {}}}],
        "degraded_servers": ["ws-down"],
    }
    merged = merge_catalog_payloads(base, overlay)
    assert len(merged["tools"]) == 1
    assert merged["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert set(merged["degraded_servers"]) == {"global-down", "ws-down"}


def test_resolve_hook_request_config_merges_workspace_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    global_cfg = home / "config.yaml"
    global_cfg.parent.mkdir(parents=True, exist_ok=True)
    global_cfg.write_text("pruning:\n  tools:\n    enabled: true\n", encoding="utf-8")

    ws_cfg = tmp_path / ".cursor" / "cyt" / "config" / "config.yaml"
    ws_cfg.parent.mkdir(parents=True)
    ws_cfg.write_text(
        "pruning:\n  tools:\n    hook:\n      tools_from: [cyt_mcp]\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "cyt.hook.workspace_config.DEFAULT_USER_CONFIG_PATH",
        global_cfg,
    )
    monkeypatch.setattr(
        "cyt.hook.workspace_config.load_config",
        lambda path=None: __import__("yaml").safe_load(global_cfg.read_text(encoding="utf-8")),
    )

    payload = {"workspace_roots": [str(tmp_path.resolve())]}
    merged, workspace = resolve_hook_request_config(payload, "cursor")
    assert workspace == tmp_path.resolve()
    assert merged["pruning"]["tools"]["hook"]["tools_from"] == ["cyt_mcp"]


def test_set_hook_workspace_in_config(tmp_path: Path) -> None:
    config = {"pruning": {"tools": {"enabled": True}}}
    merged = set_hook_workspace_in_config(config, tmp_path)
    assert Path(merged[HOOK_WORKSPACE_CONFIG_KEY]) == tmp_path.resolve()


def test_effective_permissions_unions_global_and_workspace_layers(tmp_path: Path) -> None:
    from cyt.permissions.merge import effective_mcp_permissions, effective_skills_permissions

    global_cfg = {
        "mcp": {"permissions": {"deny": ["global-mcp"], "allow": []}},
        "skills": {"permissions": {"deny": ["global-skill"], "allow": []}},
        "agents": {
            "cursor": {
                "mcp": {"permissions": {"deny": ["agent-mcp"]}},
                "skills": {"permissions": {"deny": ["agent-skill"]}},
            },
        },
    }
    workspace_cfg = {
        "mcp": {"permissions": {"deny": ["workspace-mcp"]}},
        "skills": {"permissions": {"deny": ["workspace-skill"]}},
    }

    mcp = effective_mcp_permissions(
        agent="cursor",
        global_config=global_cfg,
        workspace_config=workspace_cfg,
        workspace_root=tmp_path,
    )
    skills = effective_skills_permissions(
        agent="cursor",
        global_config=global_cfg,
        workspace_config=workspace_cfg,
        workspace_root=tmp_path,
    )

    assert set(mcp.deny) == {"global-mcp", "agent-mcp", "workspace-mcp"}
    assert set(skills.deny) == {"global-skill", "agent-skill", "workspace-skill"}

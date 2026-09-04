"""Resolve effective MCP/skills permissions with union deny merge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.config import DEFAULT_USER_CONFIG_PATH, deep_merge, load_config
from cyt.hook.install_scope import CytInstallScope
from cyt.permissions.schema import (
    EffectivePermissions,
    McpPermissions,
    PermissionLists,
    SkillsPermissions,
)


def _permissions_block(raw: object) -> PermissionLists:
    return PermissionLists.from_raw(raw)


def _mcp_permissions_from_config(config: dict[str, Any]) -> PermissionLists:
    mcp = config.get("mcp")
    if not isinstance(mcp, dict):
        return PermissionLists()
    return _permissions_block(mcp.get("permissions"))


def _skills_permissions_from_config(config: dict[str, Any]) -> PermissionLists:
    skills = config.get("skills")
    if not isinstance(skills, dict):
        return PermissionLists()
    return _permissions_block(skills.get("permissions"))


def _agent_mcp_permissions(config: dict[str, Any], agent: str) -> PermissionLists:
    agents = config.get("agents")
    if not isinstance(agents, dict):
        return PermissionLists()
    agent_block = agents.get(agent)
    if not isinstance(agent_block, dict):
        return PermissionLists()
    mcp = agent_block.get("mcp")
    if not isinstance(mcp, dict):
        return PermissionLists()
    return _permissions_block(mcp.get("permissions"))


def _agent_skills_permissions(config: dict[str, Any], agent: str) -> PermissionLists:
    agents = config.get("agents")
    if not isinstance(agents, dict):
        return PermissionLists()
    agent_block = agents.get(agent)
    if not isinstance(agent_block, dict):
        return PermissionLists()
    skills = agent_block.get("skills")
    if not isinstance(skills, dict):
        return PermissionLists()
    return _permissions_block(skills.get("permissions"))


def _union_string_lists(*layers: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    merged: list[str] = []
    for layer in layers:
        for item in layer:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return tuple(merged)


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def load_workspace_all_agents_config_overlay(
    *,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    scope = CytInstallScope(
        workspace_root=workspace_root or CytInstallScope.from_cwd().workspace_root,
    )
    path = scope.resolve_workspace_all_agents_cyt_config_path()
    if path is None:
        return {}
    return _load_yaml_dict(path)


def load_workspace_config_overlay(
    agent: str,
    *,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    from cyt.permissions.paths import is_all_agents

    if is_all_agents(agent):
        return load_workspace_all_agents_config_overlay(workspace_root=workspace_root)
    scope = CytInstallScope(
        workspace_root=workspace_root or CytInstallScope.from_cwd().workspace_root,
    )
    path = scope.resolve_workspace_cyt_config_path(agent)
    if path is None or not path.is_file():
        return {}
    return _load_yaml_dict(path)


def _effective_permissions_all_agents(
    global_cfg: dict[str, Any],
    ws_cfg: dict[str, Any],
) -> EffectivePermissions:
    mcp_layers = (
        _mcp_permissions_from_config(global_cfg).deny,
        _mcp_permissions_from_config(ws_cfg).deny,
    )
    skills_layers = (
        _skills_permissions_from_config(global_cfg).deny,
        _skills_permissions_from_config(ws_cfg).deny,
    )
    mcp_allow = _union_string_lists(
        _mcp_permissions_from_config(global_cfg).allow,
        _mcp_permissions_from_config(ws_cfg).allow,
    )
    skills_allow = _union_string_lists(
        _skills_permissions_from_config(global_cfg).allow,
        _skills_permissions_from_config(ws_cfg).allow,
    )
    return EffectivePermissions(
        mcp=McpPermissions(deny=_union_string_lists(*mcp_layers), allow=mcp_allow),
        skills=SkillsPermissions(
            deny=_union_string_lists(*skills_layers),
            allow=skills_allow,
        ),
    )


def _resolve_workspace_all_agents_config(
    workspace_config: dict[str, Any] | None,
    workspace_root: Path | None,
) -> dict[str, Any]:
    if workspace_config is not None:
        return workspace_config
    if workspace_root is not None:
        return load_workspace_all_agents_config_overlay(workspace_root=workspace_root)
    scope = CytInstallScope.from_cwd()
    if scope.has_workspace:
        return load_workspace_all_agents_config_overlay(workspace_root=scope.workspace_root)
    return {}


def _resolve_workspace_agent_config(
    resolved_agent: str,
    workspace_config: dict[str, Any] | None,
    workspace_root: Path | None,
) -> dict[str, Any]:
    if workspace_config is not None:
        return workspace_config
    if workspace_root is not None:
        return load_workspace_config_overlay(resolved_agent, workspace_root=workspace_root)
    scope = CytInstallScope.from_cwd()
    if scope.has_workspace:
        return load_workspace_config_overlay(
            resolved_agent,
            workspace_root=scope.workspace_root,
        )
    return {}


def _effective_permissions_for_agent(
    resolved_agent: str,
    global_cfg: dict[str, Any],
    *,
    workspace_config: dict[str, Any] | None,
    workspace_root: Path | None,
) -> EffectivePermissions:
    shared_ws_cfg = load_workspace_all_agents_config_overlay(workspace_root=workspace_root)
    if not shared_ws_cfg and workspace_root is None:
        scope = CytInstallScope.from_cwd()
        if scope.has_workspace:
            shared_ws_cfg = load_workspace_all_agents_config_overlay(
                workspace_root=scope.workspace_root,
            )

    agent_ws_cfg = _resolve_workspace_agent_config(
        resolved_agent,
        workspace_config,
        workspace_root,
    )

    mcp_layers = (
        _mcp_permissions_from_config(global_cfg).deny,
        _agent_mcp_permissions(global_cfg, resolved_agent).deny,
        _mcp_permissions_from_config(shared_ws_cfg).deny,
        _mcp_permissions_from_config(agent_ws_cfg).deny,
        _agent_mcp_permissions(agent_ws_cfg, resolved_agent).deny,
    )
    skills_layers = (
        _skills_permissions_from_config(global_cfg).deny,
        _agent_skills_permissions(global_cfg, resolved_agent).deny,
        _skills_permissions_from_config(shared_ws_cfg).deny,
        _skills_permissions_from_config(agent_ws_cfg).deny,
        _agent_skills_permissions(agent_ws_cfg, resolved_agent).deny,
    )

    mcp_allow = _union_string_lists(
        _mcp_permissions_from_config(global_cfg).allow,
        _agent_mcp_permissions(global_cfg, resolved_agent).allow,
        _mcp_permissions_from_config(shared_ws_cfg).allow,
        _mcp_permissions_from_config(agent_ws_cfg).allow,
        _agent_mcp_permissions(agent_ws_cfg, resolved_agent).allow,
    )
    skills_allow = _union_string_lists(
        _skills_permissions_from_config(global_cfg).allow,
        _agent_skills_permissions(global_cfg, resolved_agent).allow,
        _skills_permissions_from_config(shared_ws_cfg).allow,
        _skills_permissions_from_config(agent_ws_cfg).allow,
        _agent_skills_permissions(agent_ws_cfg, resolved_agent).allow,
    )

    return EffectivePermissions(
        mcp=McpPermissions(deny=_union_string_lists(*mcp_layers), allow=mcp_allow),
        skills=SkillsPermissions(deny=_union_string_lists(*skills_layers), allow=skills_allow),
    )


def effective_permissions(
    *,
    agent: str,
    global_config: dict[str, Any] | None = None,
    workspace_config: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
) -> EffectivePermissions:
    """Union deny lists from global, agent-specific, and workspace layers."""
    from cyt.permissions.paths import is_all_agents, normalize_agent

    raw_agent = (agent or "all").strip().lower() or "all"
    global_cfg = (
        global_config if global_config is not None else load_config(DEFAULT_USER_CONFIG_PATH)
    )

    if is_all_agents(raw_agent):
        ws_cfg = _resolve_workspace_all_agents_config(workspace_config, workspace_root)
        return _effective_permissions_all_agents(global_cfg, ws_cfg)

    resolved_agent = normalize_agent(raw_agent)
    return _effective_permissions_for_agent(
        resolved_agent,
        global_cfg,
        workspace_config=workspace_config,
        workspace_root=workspace_root,
    )


def effective_mcp_permissions(
    *,
    agent: str,
    global_config: dict[str, Any] | None = None,
    workspace_config: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
) -> McpPermissions:
    """Return merged MCP deny/allow lists for *agent*."""
    return effective_permissions(
        agent=agent,
        global_config=global_config,
        workspace_config=workspace_config,
        workspace_root=workspace_root,
    ).mcp


def effective_skills_permissions(
    *,
    agent: str,
    global_config: dict[str, Any] | None = None,
    workspace_config: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
) -> SkillsPermissions:
    """Return merged skills deny/allow lists for *agent*."""
    return effective_permissions(
        agent=agent,
        global_config=global_config,
        workspace_config=workspace_config,
        workspace_root=workspace_root,
    ).skills


def merged_hook_config(
    agent: str,
    *,
    global_config: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Deep-merge global + workspace config (for non-permission settings)."""
    global_cfg = (
        global_config if global_config is not None else load_config(DEFAULT_USER_CONFIG_PATH)
    )
    ws_cfg = load_workspace_config_overlay(agent, workspace_root=workspace_root)
    if not ws_cfg:
        return global_cfg
    return deep_merge(global_cfg, ws_cfg)

"""Path resolution for permissions config read/write."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from cyt.config import resolve_setup_config_path
from cyt.hook.install_scope import CytInstallScope

PermissionScope = Literal["global", "workspace"]
InventoryScope = Literal["global", "workspace", "effective"]
PermissionAgentTarget = Literal["cursor", "claude", "codex", "all"]

VALID_AGENTS: tuple[str, ...] = ("cursor", "claude", "codex")
ALL_AGENTS = "all"


def is_all_agents(agent: str | None) -> bool:
    text = (agent or ALL_AGENTS).strip().lower() or ALL_AGENTS
    return text == ALL_AGENTS


def resolve_inventory_agent(agent: str | None) -> str:
    """Concrete harness agent for MCP/skills inventory and workspace paths."""
    if is_all_agents(agent):
        return "cursor"
    return normalize_agent(agent)


def normalize_agent(agent: str | None) -> str:
    text = (agent or "cursor").strip().lower() or "cursor"
    if text not in VALID_AGENTS:
        raise ValueError(f"Unknown agent {agent!r}; expected cursor, claude, or codex")
    return text


def normalize_agent_target(agent: str | None) -> PermissionAgentTarget:
    text = (agent or "all").strip().lower() or "all"
    if text == "all":
        return "all"
    normalize_agent(text)
    return cast(PermissionAgentTarget, text)


def permissions_config_path(
    scope: PermissionScope,
    *,
    agent: str = "cursor",
    global_config_path: Path | None = None,
    workspace_root: Path | None = None,
) -> Path:
    if scope == "global":
        return resolve_setup_config_path(global_config_path)
    install = CytInstallScope(
        workspace_root=workspace_root or CytInstallScope.from_cwd().workspace_root,
    )
    if not install.has_workspace:
        raise ValueError("No workspace detected; use --scope global or run from a project root")
    if is_all_agents(agent):
        path = install.workspace_all_agents_cyt_config_path()
        if path is None:
            raise ValueError("Could not resolve shared workspace cyt config path")
        return path
    resolved_agent = normalize_agent(agent)
    path = install.resolve_workspace_cyt_config_path(resolved_agent)
    if path is None:
        cyt_dir = install.workspace_cyt_dir(resolved_agent)
        if cyt_dir is None:
            raise ValueError("Could not resolve workspace cyt config path")
        path = cyt_dir / "config" / "config.yaml"
    return path


def mcp_server_defs_path(
    *,
    agent: str,
    scope: PermissionScope,
    workspace_root: Path | None = None,
) -> Path:
    resolved_agent = resolve_inventory_agent(agent)
    install = CytInstallScope(
        workspace_root=workspace_root or CytInstallScope.from_cwd().workspace_root,
    )
    if scope == "workspace":
        if not install.has_workspace:
            raise ValueError("No workspace detected for workspace-scoped MCP defs")
        path = install.workspace_server_defs_path(resolved_agent)
        if path is None:
            raise ValueError("Could not resolve workspace MCP server defs path")
        return path
    return install.global_server_defs_path(resolved_agent)

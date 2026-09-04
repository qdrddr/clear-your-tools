"""Sync legacy ``enabled`` flags in cyt MCP server defs JSON with permissions policy."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

from cyt.permissions.paths import (
    PermissionAgentTarget,
    PermissionScope,
    mcp_server_defs_path,
    normalize_agent,
)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    replaced = False
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        replaced = True
    finally:
        if not replaced:
            tmp.unlink(missing_ok=True)


def disabled_server_names(servers: dict[str, Any]) -> list[str]:
    """Return backend server names with an explicit ``enabled: false`` (or equivalent)."""
    from cyt_mcp.config import is_mcp_server_enabled

    names: list[str] = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        if not is_mcp_server_enabled(spec):
            names.append(str(name).strip())
    return [name for name in names if name]


def set_mcp_server_enabled_flag(
    server: str,
    enabled: bool,
    *,
    agent: str,
    scope: PermissionScope,
    workspace_root: Path | None = None,
) -> bool:
    """Mirror permissions state into ``enabled`` on the cyt MCP defs JSON file."""
    path = mcp_server_defs_path(agent=agent, scope=scope, workspace_root=workspace_root)
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    name = server.strip()
    spec = servers.get(name)
    if not isinstance(spec, dict):
        return False
    updated = dict(spec)
    updated["enabled"] = enabled
    servers = dict(servers)
    servers[name] = updated
    payload = dict(payload)
    payload["mcpServers"] = servers
    _atomic_write_json(path, payload)
    return True


def import_disabled_servers_to_deny(
    servers: list[str],
    *,
    scope: PermissionScope,
    agent: str,
    agent_target: PermissionAgentTarget | None = None,
    global_config_path: Path | None = None,
    workspace_root: Path | None = None,
) -> Path | None:
    """Add explicitly disabled MCP servers to config.yaml deny (idempotent)."""
    from cyt.permissions.editor import _load_config_dict, _save_lists, load_permissions_lists
    from cyt.permissions.paths import permissions_config_path

    names = [str(name).strip() for name in servers if str(name).strip()]
    if not names:
        return None
    resolved_agent = normalize_agent(agent)
    target: PermissionAgentTarget = (
        agent_target if agent_target is not None else cast(PermissionAgentTarget, resolved_agent)
    )

    config_path = permissions_config_path(
        scope,
        agent=resolved_agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )
    existing = _load_config_dict(config_path)
    deny, allow = load_permissions_lists(existing, kind="mcp", agent_target=target)
    for name in names:
        if name not in deny:
            deny.append(name)
    return _save_lists(
        scope=scope,
        kind="mcp",
        agent_target=target,
        deny=deny,
        allow=allow,
        agent=resolved_agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )

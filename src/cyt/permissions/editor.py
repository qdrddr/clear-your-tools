"""Read/write cyt-native permission overlays in config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from cyt.config import save_user_config
from cyt.permissions.match import (
    format_skill_path_permission_entry,
    parse_mcp_deny_entry,
    parse_skill_permission_entry,
    skill_path_matches_rule,
)
from cyt.permissions.paths import (
    PermissionAgentTarget,
    PermissionScope,
    permissions_config_path,
)

PermissionKind = Literal["mcp", "skills"]


def _overlay_path(
    kind: PermissionKind,
    agent_target: PermissionAgentTarget,
) -> tuple[str, ...]:
    if agent_target == "all":
        return (kind, "permissions")
    return ("agents", agent_target, kind, "permissions")


def _get_nested(config: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    current: dict[str, Any] = config
    for key in keys[:-1]:
        block = current.get(key)
        if not isinstance(block, dict):
            block = {}
            current[key] = block
        current = block
    return current


def _permissions_lists(
    config: dict[str, Any],
    keys: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    current = config
    for key in keys:
        block = current.get(key)
        if not isinstance(block, dict):
            return [], []
        current = block
    deny_raw = current.get("deny")
    allow_raw = current.get("allow")
    deny = (
        [str(x).strip() for x in deny_raw if str(x).strip()] if isinstance(deny_raw, list) else []
    )
    allow = (
        [str(x).strip() for x in allow_raw if str(x).strip()] if isinstance(allow_raw, list) else []
    )
    return deny, allow


def load_permissions_lists(
    config: dict[str, Any],
    *,
    kind: PermissionKind,
    agent_target: PermissionAgentTarget,
) -> tuple[list[str], list[str]]:
    return _permissions_lists(config, _overlay_path(kind, agent_target))


def _build_overlay(
    kind: PermissionKind,
    agent_target: PermissionAgentTarget,
    deny: list[str],
    allow: list[str] | None = None,
) -> dict[str, Any]:
    keys = _overlay_path(kind, agent_target)
    permissions: dict[str, Any] = {"deny": deny}
    if allow is not None:
        permissions["allow"] = allow
    overlay: dict[str, Any] = {}
    current: dict[str, Any] = overlay
    for key in keys[:-1]:
        nested: dict[str, Any] = {}
        current[key] = nested
        current = nested
    current[keys[-1]] = permissions
    return overlay


def _load_config_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _save_lists(
    *,
    scope: PermissionScope,
    kind: PermissionKind,
    agent_target: PermissionAgentTarget,
    deny: list[str],
    allow: list[str] | None,
    agent: str,
    global_config_path: Path | None,
    workspace_root: Path | None,
) -> Path:
    path = permissions_config_path(
        scope,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )
    existing = _load_config_dict(path)
    _current_deny, current_allow = load_permissions_lists(
        existing,
        kind=kind,
        agent_target=agent_target,
    )
    merged_allow = allow if allow is not None else current_allow
    overlay = _build_overlay(kind, agent_target, deny, merged_allow)
    save_user_config(path, overlay)
    return path


def _remove_server_deny_entries(deny: list[str], server: str) -> list[str]:
    server = server.strip()
    kept: list[str] = []
    for entry in deny:
        rule = parse_mcp_deny_entry(entry)
        if rule is None:
            kept.append(entry)
            continue
        if rule.server == server:
            continue
        kept.append(entry)
    return kept


def _remove_tool_deny_entry(deny: list[str], server: str, tool: str) -> list[str]:
    target = f"{server.strip()}/{tool.strip()}"
    return [entry for entry in deny if entry.strip() != target]


def disable_mcp_server(
    server: str,
    *,
    scope: PermissionScope,
    agent_target: PermissionAgentTarget,
    agent: str = "cursor",
    global_config_path: Path | None = None,
    workspace_root: Path | None = None,
) -> Path:
    path = permissions_config_path(
        scope,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )
    existing = _load_config_dict(path)
    deny, allow = load_permissions_lists(existing, kind="mcp", agent_target=agent_target)
    name = server.strip()
    if name not in deny:
        deny.append(name)
    config_path = _save_lists(
        scope=scope,
        kind="mcp",
        agent_target=agent_target,
        deny=deny,
        allow=allow,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )
    from cyt.permissions.mcp_defs import set_mcp_server_enabled_flag

    set_mcp_server_enabled_flag(
        name,
        False,
        agent=agent,
        scope=scope,
        workspace_root=workspace_root,
    )
    return config_path


def enable_mcp_server(
    server: str,
    *,
    scope: PermissionScope,
    agent_target: PermissionAgentTarget,
    agent: str = "cursor",
    global_config_path: Path | None = None,
    workspace_root: Path | None = None,
) -> Path:
    path = permissions_config_path(
        scope,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )
    existing = _load_config_dict(path)
    deny, allow = load_permissions_lists(existing, kind="mcp", agent_target=agent_target)
    deny = _remove_server_deny_entries(deny, server)
    config_path = _save_lists(
        scope=scope,
        kind="mcp",
        agent_target=agent_target,
        deny=deny,
        allow=allow,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )
    from cyt.permissions.mcp_defs import set_mcp_server_enabled_flag

    set_mcp_server_enabled_flag(
        server.strip(),
        True,
        agent=agent,
        scope=scope,
        workspace_root=workspace_root,
    )
    return config_path


def disable_mcp_tool(
    server: str,
    tool: str,
    *,
    scope: PermissionScope,
    agent_target: PermissionAgentTarget,
    agent: str = "cursor",
    global_config_path: Path | None = None,
    workspace_root: Path | None = None,
) -> Path:
    path = permissions_config_path(
        scope,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )
    existing = _load_config_dict(path)
    deny, allow = load_permissions_lists(existing, kind="mcp", agent_target=agent_target)
    entry = f"{server.strip()}/{tool.strip()}"
    if entry not in deny:
        deny.append(entry)
    return _save_lists(
        scope=scope,
        kind="mcp",
        agent_target=agent_target,
        deny=deny,
        allow=allow,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )


def enable_mcp_tool(
    server: str,
    tool: str,
    *,
    scope: PermissionScope,
    agent_target: PermissionAgentTarget,
    agent: str = "cursor",
    global_config_path: Path | None = None,
    workspace_root: Path | None = None,
) -> Path:
    path = permissions_config_path(
        scope,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )
    existing = _load_config_dict(path)
    deny, allow = load_permissions_lists(existing, kind="mcp", agent_target=agent_target)
    deny = _remove_tool_deny_entry(deny, server, tool)
    return _save_lists(
        scope=scope,
        kind="mcp",
        agent_target=agent_target,
        deny=deny,
        allow=allow,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )


def _remove_skill_name_deny_entry(deny: list[str], skill_name: str) -> list[str]:
    target = skill_name.strip()
    kept: list[str] = []
    for entry in deny:
        rule = parse_skill_permission_entry(entry)
        if rule is not None and rule.kind == "name" and rule.value == target:
            continue
        kept.append(entry)
    return kept


def _remove_skill_path_deny_entry(
    deny: list[str],
    skill_path: str | Path,
    *,
    workspace_root: Path | None = None,
) -> list[str]:
    kept: list[str] = []
    for entry in deny:
        rule = parse_skill_permission_entry(entry)
        if rule is None or rule.kind != "path":
            kept.append(entry)
            continue
        if skill_path_matches_rule(skill_path, rule.value, base=workspace_root):
            continue
        kept.append(entry)
    return kept


def disable_skill(
    skill_name: str,
    *,
    scope: PermissionScope,
    agent_target: PermissionAgentTarget,
    agent: str = "cursor",
    global_config_path: Path | None = None,
    workspace_root: Path | None = None,
    skill_path: str | Path | None = None,
) -> Path:
    path = permissions_config_path(
        scope,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )
    existing = _load_config_dict(path)
    deny, allow = load_permissions_lists(existing, kind="skills", agent_target=agent_target)
    if skill_path is not None:
        entry = format_skill_path_permission_entry(skill_path, workspace_root=workspace_root)
    else:
        entry = skill_name.strip()
    if entry not in deny:
        deny.append(entry)
    return _save_lists(
        scope=scope,
        kind="skills",
        agent_target=agent_target,
        deny=deny,
        allow=allow,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )


def enable_skill(
    skill_name: str,
    *,
    scope: PermissionScope,
    agent_target: PermissionAgentTarget,
    agent: str = "cursor",
    global_config_path: Path | None = None,
    workspace_root: Path | None = None,
    skill_path: str | Path | None = None,
) -> Path:
    path = permissions_config_path(
        scope,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )
    existing = _load_config_dict(path)
    deny, allow = load_permissions_lists(existing, kind="skills", agent_target=agent_target)
    if skill_path is not None:
        deny = _remove_skill_path_deny_entry(
            deny,
            skill_path,
            workspace_root=workspace_root,
        )
    else:
        deny = _remove_skill_name_deny_entry(deny, skill_name)
    return _save_lists(
        scope=scope,
        kind="skills",
        agent_target=agent_target,
        deny=deny,
        allow=allow,
        agent=agent,
        global_config_path=global_config_path,
        workspace_root=workspace_root,
    )


def parse_server_tool_arg(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if "/" not in text:
        raise ValueError(f"Expected SERVER/TOOL, got {value!r}")
    server, tool = text.split("/", 1)
    server = server.strip()
    tool = tool.strip()
    if not server or not tool:
        raise ValueError(f"Expected SERVER/TOOL, got {value!r}")
    return server, tool

"""Runtime permission filtering helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cyt.config import load_config, tools_hook_cyt_mcp_agent
from cyt.hook.workspace_config import hook_workspace_from_config
from cyt.permissions.inventory.skills import skill_policy_name_from_path
from cyt.permissions.match import (
    is_catalog_tool_denied,
    is_mcp_server_denied,
    is_skill_permission_denied,
)
from cyt.permissions.merge import effective_permissions
from cyt.permissions.schema import EffectivePermissions
from cyt.skills.catalog import SkillEntryRef
from cyt.skills.nodes import skill_name


def resolve_effective_permissions(
    *,
    agent: str | None = None,
    config: dict[str, Any] | None = None,
    workspace_root: Path | str | None = None,
) -> EffectivePermissions:
    cfg = config or load_config()
    resolved_agent = (agent or tools_hook_cyt_mcp_agent(cfg)).strip() or "cursor"
    ws = workspace_root
    if ws is None:
        hook_ws = hook_workspace_from_config(cfg)
        ws = hook_ws
    workspace_path = Path(str(ws)).expanduser() if ws else None
    return effective_permissions(
        agent=resolved_agent,
        workspace_root=workspace_path,
    )


def filter_mcp_servers(
    servers: dict[str, Any],
    deny_entries: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    if not deny_entries:
        return servers
    return {
        name: spec for name, spec in servers.items() if not is_mcp_server_denied(name, deny_entries)
    }


def filter_catalog_tool_dicts(
    tools: Sequence[dict[str, Any]],
    deny_entries: tuple[str, ...] | list[str],
) -> list[dict[str, Any]]:
    if not deny_entries:
        return list(tools)
    filtered: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        if is_catalog_tool_denied(name, deny_entries):
            continue
        filtered.append(tool)
    return filtered


def skill_policy_name(entry: SkillEntryRef) -> str:
    name = skill_name(entry)
    if name:
        return name
    path = Path(entry.source_path)
    if path.name.lower() == "skill.md":
        return path.parent.name
    return path.stem


def filter_skill_entries(
    entries: Sequence[SkillEntryRef],
    deny_entries: tuple[str, ...] | list[str],
    *,
    base: Path | None = None,
) -> list[SkillEntryRef]:
    if not deny_entries:
        return list(entries)
    return [
        entry
        for entry in entries
        if not is_skill_permission_denied(
            skill_name=skill_policy_name(entry),
            skill_path=entry.source_path,
            deny_entries=deny_entries,
            base=base,
        )
    ]


def filter_matched_skills_by_permissions(
    matches: Sequence[Any],
    deny_entries: tuple[str, ...] | list[str],
    *,
    base: Path | None = None,
) -> list[Any]:
    if not deny_entries:
        return list(matches)
    filtered: list[Any] = []
    for match in matches:
        name = getattr(match, "name", None)
        file_path = getattr(match, "file_path", None)
        policy_name = ""
        skill_path: Path | None = None
        if isinstance(name, str) and name.strip():
            policy_name = name.strip()
        elif isinstance(file_path, str) and file_path.strip():
            skill_path = Path(file_path)
            policy_name, _ = skill_policy_name_from_path(skill_path)
        else:
            policy_name = str(getattr(match, "doc_id", "") or "")
        if isinstance(file_path, str) and file_path.strip():
            skill_path = Path(file_path)
        if is_skill_permission_denied(
            skill_name=policy_name,
            skill_path=skill_path,
            deny_entries=deny_entries,
            base=base,
        ):
            continue
        filtered.append(match)
    return filtered

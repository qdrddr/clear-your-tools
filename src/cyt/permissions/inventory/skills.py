"""List skills for permissions CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cyt.config import DEFAULT_USER_CONFIG_PATH, load_config
from cyt.permissions.match import is_skill_permission_denied
from cyt.permissions.merge import effective_permissions
from cyt.permissions.paths import InventoryScope, PermissionScope, resolve_inventory_agent
from cyt.skills.agents import directory_belongs_to_agent
from cyt.skills.catalog import _walk_skill_md_files
from cyt.skills.frontmatter import skill_name_from_frontmatter

__all__ = [
    "SkillInventoryItem",
    "SkillSource",
    "directory_belongs_to_agent",
    "enumerate_skill_names",
    "list_skills",
    "skill_policy_name_from_path",
]

SkillSource = Literal["user", "workspace"]


@dataclass(frozen=True)
class SkillInventoryItem:
    name: str
    path: str
    enabled: bool
    name_from_frontmatter: bool
    source: SkillSource | None = None


def _inventory_layers(scope: InventoryScope) -> tuple[PermissionScope, ...]:
    if scope == "effective":
        return ("user", "workspace")
    return (scope,)


def _read_frontmatter(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[: end + 4]


def skill_policy_name_from_path(path: Path, *, frontmatter: str | None = None) -> tuple[str, bool]:
    """Return policy name and whether it came from frontmatter."""
    fm = frontmatter if frontmatter is not None else _read_frontmatter(path)
    name = skill_name_from_frontmatter(fm)
    if name:
        return name, True
    if path.name.lower() == "skill.md":
        return path.parent.name, False
    return path.stem, False


def _discovery_config_for_layer(
    layer: PermissionScope,
    *,
    agent: str,
    workspace_root: Path | None,
    global_config: dict | None,
) -> tuple[dict, Path | None]:
    base_config = (
        global_config if global_config is not None else load_config(DEFAULT_USER_CONFIG_PATH)
    )
    if layer == "user":
        return base_config, None
    if workspace_root is None:
        return {}, None
    from cyt.hook.workspace_config import resolve_hook_request_config

    resolved_agent = resolve_inventory_agent(agent)
    merged, _workspace = resolve_hook_request_config(
        {"workspace_root": str(workspace_root)},
        resolved_agent,
        base_config=base_config,
    )
    return merged, workspace_root


def _enumerate_skill_rows_for_layer(
    layer: PermissionScope,
    *,
    agent: str | None,
    workspace_root: Path | None,
    global_config: dict | None,
) -> list[tuple[str, Path, bool, SkillSource]]:
    from cyt.skills.directories import resolve_skill_directories

    policy_agent = (agent or "all").strip().lower() or "all"
    cfg, layer_workspace = _discovery_config_for_layer(
        layer,
        agent=policy_agent,
        workspace_root=workspace_root,
        global_config=global_config,
    )
    if not cfg:
        return []

    directories = resolve_skill_directories(
        cfg,
        agent=policy_agent,
        workspace_root=layer_workspace,
        include_platform_defaults=True,
    )
    rows: list[tuple[str, Path, bool, SkillSource]] = []
    source: SkillSource = "user" if layer == "user" else "workspace"
    for path in _walk_skill_md_files([str(directory) for directory in directories]):
        if path.name.casefold() != "skill.md":
            continue
        frontmatter = _read_frontmatter(path)
        name, from_frontmatter = skill_policy_name_from_path(path, frontmatter=frontmatter)
        rows.append((name, path, from_frontmatter, source))
    rows.sort(key=lambda row: row[0].casefold())
    return rows


def enumerate_skill_names(
    config: dict | None = None,
    *,
    agent: str | None = None,
    scope: InventoryScope | None = None,
    workspace_root: Path | None = None,
    global_config: dict | None = None,
) -> list[tuple[str, Path, bool]]:
    """Return (policy_name, path, from_frontmatter) for each skill file."""
    if scope is not None or workspace_root is not None or global_config is not None:
        resolved_scope: InventoryScope = scope or "effective"
        merged_rows = _merge_skill_inventory_rows(
            agent=agent,
            scope=resolved_scope,
            workspace_root=workspace_root,
            global_config=global_config or config,
        )
        return [
            (name, path, from_frontmatter) for name, path, from_frontmatter, _source in merged_rows
        ]

    from cyt.config import skills_directories_for_agent

    cfg = config or load_config()
    legacy_rows: list[tuple[str, Path, bool]] = []
    seen: set[str] = set()
    for path in _walk_skill_md_files(skills_directories_for_agent(cfg, agent=agent)):
        if path.name.casefold() != "skill.md":
            continue
        frontmatter = _read_frontmatter(path)
        name, from_frontmatter = skill_policy_name_from_path(path, frontmatter=frontmatter)
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        legacy_rows.append((name, path, from_frontmatter))
    legacy_rows.sort(key=lambda row: row[0].casefold())
    return legacy_rows


def _merge_skill_inventory_rows(
    *,
    agent: str | None,
    scope: InventoryScope,
    workspace_root: Path | None,
    global_config: dict | None,
) -> list[tuple[str, Path, bool, SkillSource]]:
    if scope == "effective":
        merged: dict[str, tuple[str, Path, bool, SkillSource]] = {}
        for layer in _inventory_layers(scope):
            if layer == "workspace" and workspace_root is None:
                continue
            for name, path, from_frontmatter, source in _enumerate_skill_rows_for_layer(
                layer,
                agent=agent,
                workspace_root=workspace_root,
                global_config=global_config,
            ):
                key = name.casefold()
                if layer == "workspace" or key not in merged:
                    merged[key] = (name, path, from_frontmatter, source)
        rows = list(merged.values())
        rows.sort(key=lambda row: row[0].casefold())
        return rows

    if scope == "workspace" and workspace_root is None:
        return []
    return _enumerate_skill_rows_for_layer(
        scope,
        agent=agent,
        workspace_root=workspace_root,
        global_config=global_config,
    )


def list_skills(
    *,
    agent: str = "cursor",
    scope: InventoryScope = "effective",
    workspace_root: Path | None = None,
    config: dict | None = None,
    global_config: dict | None = None,
) -> tuple[list[SkillInventoryItem], list[SkillInventoryItem]]:
    del config
    policy_agent = (agent or "all").strip().lower() or "all"
    effective = effective_permissions(
        agent=policy_agent,
        workspace_root=workspace_root,
        global_config=global_config,
    )
    enabled: list[SkillInventoryItem] = []
    disabled: list[SkillInventoryItem] = []
    for name, path, from_frontmatter, source in _merge_skill_inventory_rows(
        agent=policy_agent,
        scope=scope,
        workspace_root=workspace_root,
        global_config=global_config,
    ):
        item = SkillInventoryItem(
            name=name,
            path=str(path),
            enabled=not is_skill_permission_denied(
                skill_name=name,
                skill_path=path,
                deny_entries=effective.skills.deny,
                base=workspace_root,
            ),
            name_from_frontmatter=from_frontmatter,
            source=source,
        )
        if item.enabled:
            enabled.append(item)
        else:
            disabled.append(item)
    return enabled, disabled

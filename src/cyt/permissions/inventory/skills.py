"""List skills for permissions CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cyt.config import load_config, skills_directories_for_agent
from cyt.permissions.match import is_skill_permission_denied
from cyt.permissions.merge import effective_permissions
from cyt.skills.agents import directory_belongs_to_agent
from cyt.skills.catalog import _walk_skill_md_files
from cyt.skills.frontmatter import skill_name_from_frontmatter

__all__ = [
    "SkillInventoryItem",
    "directory_belongs_to_agent",
    "enumerate_skill_names",
    "list_skills",
    "skill_policy_name_from_path",
    "skills_directories_for_agent",
]


@dataclass(frozen=True)
class SkillInventoryItem:
    name: str
    path: str
    enabled: bool
    name_from_frontmatter: bool


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


def enumerate_skill_names(
    config: dict | None = None,
    *,
    agent: str | None = None,
) -> list[tuple[str, Path, bool]]:
    """Return (policy_name, path, from_frontmatter) for each skill file."""
    cfg = config or load_config()
    rows: list[tuple[str, Path, bool]] = []
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
        rows.append((name, path, from_frontmatter))
    rows.sort(key=lambda row: row[0].casefold())
    return rows


def list_skills(
    *,
    agent: str = "cursor",
    workspace_root: Path | None = None,
    config: dict | None = None,
    global_config: dict | None = None,
) -> tuple[list[SkillInventoryItem], list[SkillInventoryItem]]:
    cfg = config or load_config()
    policy_agent = (agent or "all").strip().lower() or "all"
    effective = effective_permissions(
        agent=policy_agent,
        workspace_root=workspace_root,
        global_config=global_config,
    )
    enabled: list[SkillInventoryItem] = []
    disabled: list[SkillInventoryItem] = []
    for name, path, from_frontmatter in enumerate_skill_names(cfg, agent=policy_agent):
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
        )
        if item.enabled:
            enabled.append(item)
        else:
            disabled.append(item)
    return enabled, disabled

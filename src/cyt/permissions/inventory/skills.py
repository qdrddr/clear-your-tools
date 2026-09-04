"""List skills for permissions CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cyt.config import load_config, skills_directories
from cyt.permissions.match import is_skill_permission_denied
from cyt.permissions.merge import effective_permissions
from cyt.permissions.paths import is_all_agents, normalize_agent
from cyt.skills.catalog import _walk_skill_md_files
from cyt.skills.frontmatter import skill_name_from_frontmatter

_DEFAULT_AGENT_SKILL_DIRS: dict[str, tuple[str, ...]] = {
    "cursor": ("~/.cursor/skills", ".cursor/skills"),
    "claude": ("~/.claude/skills", ".claude/skills"),
    "codex": ("~/.codex/skills", ".codex/skills"),
}


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


def directory_belongs_to_agent(directory: str, agent: str) -> bool:
    """Return True when *directory* is a skills tree for *agent*."""
    parts = [part.casefold() for part in Path(directory).expanduser().parts]
    agent_name = normalize_agent(agent).casefold()
    dotted = f".{agent_name}"
    for index, part in enumerate(parts):
        if part != "skills" or index == 0:
            continue
        parent = parts[index - 1]
        if parent == agent_name or parent == dotted:
            return True
    return False


def skills_directories_for_agent(
    config: dict | None = None,
    *,
    agent: str | None = None,
) -> list[str]:
    """Return configured skills directories scoped to one agent harness."""
    cfg = config or load_config()
    all_dirs = skills_directories(cfg)
    if agent is None or is_all_agents(agent):
        return all_dirs
    resolved = normalize_agent(agent)
    filtered = [
        directory for directory in all_dirs if directory_belongs_to_agent(directory, resolved)
    ]
    if filtered:
        return filtered
    return [str(Path(path).expanduser()) for path in _DEFAULT_AGENT_SKILL_DIRS.get(resolved, ())]


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
) -> tuple[list[SkillInventoryItem], list[SkillInventoryItem]]:
    cfg = config or load_config()
    policy_agent = (agent or "all").strip().lower() or "all"
    effective = effective_permissions(agent=policy_agent, workspace_root=workspace_root)
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

"""Workspace-aware skill directory resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

WORKSPACE_SKILLS_DIR = ".agents/skills"

_AGENT_SKILL_PAIRS: dict[str, tuple[str, str]] = {
    "cursor": (".cursor/skills", "~/.cursor/skills"),
    "claude": (".claude/skills", "~/.claude/skills"),
    "codex": (".codex/skills", "~/.codex/skills"),
}

_GLOBAL_SKILL_PAIRS: tuple[tuple[str, str], ...] = ((WORKSPACE_SKILLS_DIR, "~/.agents/skills"),)


def merge_skills_directory_lists(
    existing: list[str],
    new_dirs: list[str],
) -> tuple[list[str], bool]:
    """Append skill directory paths from *new_dirs* when not already present."""
    merged = [str(path) for path in existing if str(path).strip()]
    seen = {str(Path(path).expanduser()) for path in merged}
    changed = False
    for raw in new_dirs:
        text = str(raw).strip()
        if not text:
            continue
        expanded = str(Path(text).expanduser())
        if expanded in seen:
            continue
        merged.append(text)
        seen.add(expanded)
        changed = True
    return merged, changed


def resolve_skill_directory_path(raw: str, workspace_root: Path | None) -> Path | None:
    text = str(raw).strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute() and workspace_root is not None:
        path = workspace_root / path
    try:
        return path.resolve()
    except OSError:
        return None


def _append_directory(
    directories: list[Path],
    seen: set[Path],
    raw: str,
    workspace_root: Path | None,
) -> None:
    resolved = resolve_skill_directory_path(raw, workspace_root)
    if resolved is None or resolved in seen:
        return
    seen.add(resolved)
    directories.append(resolved)


def resolve_skill_directories(
    config: dict[str, Any],
    *,
    agent: str | None,
    workspace_root: Path | None,
    include_platform_defaults: bool = False,
) -> list[Path]:
    """Return deduped absolute skill roots for *agent* and *workspace_root*."""
    from cyt.config import inject_via_agents, skills_directories_for_agent

    directories: list[Path] = []
    seen: set[Path] = set()

    agents = [agent] if agent else list(inject_via_agents())

    for resolved_agent in agents:
        for raw in skills_directories_for_agent(config, agent=resolved_agent):
            _append_directory(directories, seen, raw, workspace_root)

    if not include_platform_defaults:
        return directories

    for project_rel, home_rel in _GLOBAL_SKILL_PAIRS:
        if workspace_root is not None:
            _append_directory(directories, seen, project_rel, workspace_root)
        _append_directory(directories, seen, home_rel, workspace_root)

    for resolved_agent in agents:
        pair = _AGENT_SKILL_PAIRS.get(resolved_agent)
        if pair is None:
            continue
        project_rel, home_rel = pair
        if workspace_root is not None:
            _append_directory(directories, seen, project_rel, workspace_root)
        _append_directory(directories, seen, home_rel, workspace_root)
        if resolved_agent == "cursor":
            _append_directory(
                directories,
                seen,
                str(Path.home() / ".cursor" / "skills-cursor"),
                workspace_root,
            )

    return directories


def ensure_workspace_skills_config(workspace_root: Path) -> bool:
    """Add ``.agents/skills`` to workspace CYT config when skills injection is enabled."""
    from cyt.config import save_user_config
    from cyt.hook.install_scope import CytInstallScope
    from cyt.migrations.workspace_paths import ensure_canonical_workspace_config

    scope = CytInstallScope(workspace_root=workspace_root)
    if not scope.has_workspace:
        return False

    ensure_canonical_workspace_config(scope)
    config_path = scope.workspace_all_agents_cyt_config_path()
    if config_path is None:
        return False

    skills_dir = workspace_root / WORKSPACE_SKILLS_DIR
    skills_dir.mkdir(parents=True, exist_ok=True)

    existing: list[str] = []
    if config_path.is_file():
        import yaml

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            skills_cfg = raw.get("skills")
            if isinstance(skills_cfg, dict):
                dirs = skills_cfg.get("directories")
                if isinstance(dirs, list):
                    existing = [str(path) for path in dirs if str(path).strip()]

    merged, changed = merge_skills_directory_lists(existing, [WORKSPACE_SKILLS_DIR])
    if not changed and WORKSPACE_SKILLS_DIR in existing:
        return False

    overlay: dict[str, Any] = {"skills": {"directories": merged}}
    save_user_config(config_path, overlay, apply_bundled_sections=False)
    return True

"""Collect agent skill files for cyt-client (stdlib only)."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any

from cyt_client.agent import infer_harness_agent
from cyt_client.paths import expand_home_path


def _payload_cwd(data: dict[str, Any]) -> Path:
    raw = data.get("cwd")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    roots = data.get("workspace_roots")
    if isinstance(roots, list) and roots:
        first = roots[0]
        if isinstance(first, str) and first.strip():
            return Path(first.strip()).expanduser()
    nested = data.get("payload")
    if isinstance(nested, dict):
        return _payload_cwd(nested)
    return Path.cwd()


def infer_launch_agent(data: dict[str, Any]) -> str | None:
    """Resolve the active agent from harness env/payload signals."""
    return infer_harness_agent(data)


def _load_merged_config_for_payload(data: dict[str, Any]) -> dict[str, Any]:
    try:
        cyt_config = importlib.import_module("cyt.config")
        hook_workspace_config = importlib.import_module("cyt.hook.workspace_config")
        from cyt_client.rules_file import workspace_root_from_payload

        load_config = cyt_config.load_config
        resolve_hook_request_config = hook_workspace_config.resolve_hook_request_config
        set_hook_workspace_in_config = hook_workspace_config.set_hook_workspace_in_config

        agent = infer_launch_agent(data) or "cursor"
        merged_config, workspace = resolve_hook_request_config(
            data,
            agent,
            base_config=load_config(),
        )
        if workspace is None:
            workspace = workspace_root_from_payload(data)
        if workspace is not None:
            merged_config = set_hook_workspace_in_config(merged_config, workspace)
        if not isinstance(merged_config, dict):
            return {}
        return merged_config
    except ImportError:
        return {}


def _merge_skill_directory_paths(existing: list[Path], extra: list[Path]) -> list[Path]:
    merged = list(existing)
    seen: set[str] = set()
    for path in merged:
        try:
            seen.add(str(path.expanduser().resolve()))
        except OSError:
            seen.add(str(path.expanduser()))
    for path in extra:
        try:
            key = str(path.expanduser().resolve())
        except OSError:
            key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        merged.append(path)
    return merged


def _load_workspace_skills_directories(workspace: Path) -> list[str] | None:
    config_path = workspace / ".agents" / "cyt" / "config" / "config.yaml"
    if not config_path.is_file():
        return None
    try:
        import yaml
    except ImportError:
        return None
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    skills = raw.get("skills")
    if not isinstance(skills, dict):
        return None
    directories = skills.get("directories")
    if not isinstance(directories, list):
        return None
    return [str(item).strip() for item in directories if str(item).strip()]


def _resolve_skill_directory_paths(directories: list[str], workspace: Path) -> list[Path]:
    resolved: list[Path] = []
    for text in directories:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = workspace / path
        try:
            resolved.append(path.resolve())
        except OSError:
            resolved.append(path)
    return resolved


def workspace_config_skill_directories(data: dict[str, Any]) -> list[Path]:
    """Skill roots declared in workspace ``.agents/cyt/config/config.yaml``."""
    from cyt_client.rules_file import is_valid_workspace_root, workspace_root_from_payload

    workspace = workspace_root_from_payload(data)
    if workspace is None:
        workspace = _payload_cwd(data)
    if not is_valid_workspace_root(workspace):
        return []

    directories = _load_workspace_skills_directories(workspace)
    if not directories:
        return []
    return _resolve_skill_directory_paths(directories, workspace)


def _fallback_skill_directories(data: dict[str, Any]) -> list[Path]:
    """Legacy fallback when cyt is unavailable in the client process."""
    _agent_skill_dirs: dict[str, tuple[str, str]] = {
        "claude": (".claude/skills", "~/.claude/skills"),
        "codex": (".codex/skills", "~/.codex/skills"),
        "cursor": (".cursor/skills", "~/.cursor/skills"),
    }
    cwd = _payload_cwd(data)
    agent = infer_launch_agent(data)
    pairs = (
        [_agent_skill_dirs[agent]]
        if agent in _agent_skill_dirs
        else list(_agent_skill_dirs.values())
    )

    directories: list[Path] = []
    seen: set[Path] = set()
    for project_rel, home_rel in pairs:
        for candidate in (cwd / project_rel, expand_home_path(home_rel)):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            directories.append(resolved)
    if agent == "cursor":
        for candidate in (
            cwd / ".cursor" / "skills-cursor",
            expand_home_path("~/.cursor/skills-cursor"),
        ):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            directories.append(resolved)
    return _merge_skill_directory_paths(directories, workspace_config_skill_directories(data))


def _user_global_skill_directories(data: dict[str, Any]) -> list[Path]:
    """Skill roots from user/global config before workspace overlay replaces list fields."""
    try:
        cyt_config = importlib.import_module("cyt.config")
        skill_directories_mod = importlib.import_module("cyt.skills.directories")

        load_config = cyt_config.load_config
        resolve_skill_directories = skill_directories_mod.resolve_skill_directories
        agent = infer_launch_agent(data) or "cursor"
        directories = resolve_skill_directories(
            load_config(),
            agent=agent,
            workspace_root=None,
            include_platform_defaults=False,
        )
        return list(directories)
    except ImportError:
        return []


def skill_directories_for_payload(data: dict[str, Any]) -> list[Path]:
    """Agent/client skill roots plus workspace ``config.yaml`` ``skills.directories``."""
    cwd = _payload_cwd(data)
    agent = infer_launch_agent(data)
    overlay_dirs = workspace_config_skill_directories(data)
    try:
        skill_directories = importlib.import_module("cyt.skills.directories")
        from cyt_client.rules_file import workspace_root_from_payload

        resolve_skill_directories = skill_directories.resolve_skill_directories
        config = _load_merged_config_for_payload(data)
        workspace = workspace_root_from_payload(data) or cwd
        client_dirs = resolve_skill_directories(
            config,
            agent=agent,
            workspace_root=workspace,
            include_platform_defaults=True,
        )
    except ImportError:
        return _fallback_skill_directories(data)
    merged = _merge_skill_directory_paths(client_dirs, overlay_dirs)
    return _merge_skill_directory_paths(merged, _user_global_skill_directories(data))


def collect_client_skills(data: dict[str, Any]) -> list[dict[str, str]]:
    """Read skill markdown files from resolved directories; dedupe by content hash."""
    skills: list[dict[str, str]] = []
    seen_hashes: set[str] = set()

    for directory in skill_directories_for_payload(data):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            skills.append({"path": str(path.resolve()), "content": content})
    return skills


def attach_skill_directories(data: dict[str, Any]) -> dict[str, Any]:
    """Attach ``cyt_skill_directories`` with absolute paths for each skill root."""
    data["cyt_skill_directories"] = [str(path) for path in skill_directories_for_payload(data)]
    return data


def attach_client_skills(data: dict[str, Any]) -> dict[str, Any]:
    """Attach ``cyt_skills`` with path + content for each discovered skill file."""
    attach_skill_directories(data)
    data["cyt_skills"] = collect_client_skills(data)
    return data

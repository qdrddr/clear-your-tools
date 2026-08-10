"""Collect agent skill files for cyt-client (stdlib only)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cyt_client.agent import infer_harness_agent

_AGENT_SKILL_DIRS: dict[str, tuple[str, str]] = {
    "claude": (".claude/skills", "~/.claude/skills"),
    "codex": (".codex/skills", "~/.codex/skills"),
    "cursor": (".cursor/skills", "~/.cursor/skills"),
}


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


def skill_directories_for_payload(data: dict[str, Any]) -> list[Path]:
    """Project-local and user-home skill directories for the active agent."""
    cwd = _payload_cwd(data)
    agent = infer_launch_agent(data)
    pairs = [_AGENT_SKILL_DIRS[agent]] if agent is not None else list(_AGENT_SKILL_DIRS.values())

    directories: list[Path] = []
    seen: set[Path] = set()
    for project_rel, home_rel in pairs:
        for candidate in (cwd / project_rel, Path(home_rel).expanduser()):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            directories.append(resolved)
    return directories


def collect_client_skills(data: dict[str, Any]) -> list[dict[str, str]]:
    """Read skill markdown files from agent directories; dedupe by content hash."""
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


def attach_client_skills(data: dict[str, Any]) -> dict[str, Any]:
    """Attach ``cyt_skills`` with path + content for each discovered skill file."""
    data["cyt_skills"] = collect_client_skills(data)
    return data

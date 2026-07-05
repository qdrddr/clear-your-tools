"""Collect agent skill files for cyt-client (stdlib only)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

CYT_LAUNCH_AGENT_ENV = "CYT_LAUNCH_AGENT"

_AGENT_SKILL_DIRS: dict[str, tuple[str, str]] = {
    "claude": (".claude/skills", "~/.claude/skills"),
    "codex": (".codex/skills", "~/.codex/skills"),
    "cursor": (".cursor/skills", "~/.cursor/skills"),
}


def _payload_cwd(data: dict[str, Any]) -> Path:
    raw = data.get("cwd")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    return Path.cwd()


def _infer_agent_from_path_text(text: str) -> str | None:
    normalized = text.replace("\\", "/")
    if "/.codex/" in normalized or normalized.endswith("/.codex"):
        return "codex"
    if "/.claude/" in normalized or normalized.endswith("/.claude"):
        return "claude"
    if "/.cursor/" in normalized or normalized.endswith("/.cursor"):
        return "cursor"
    return None


def infer_launch_agent(data: dict[str, Any]) -> str | None:
    """Resolve the active agent from ``CYT_LAUNCH_AGENT`` or hook payload hints."""
    env_value = os.environ.get(CYT_LAUNCH_AGENT_ENV, "").strip().lower()
    if env_value in _AGENT_SKILL_DIRS:
        return env_value

    for key in ("transcript_path",):
        raw = data.get(key)
        if isinstance(raw, str) and raw.strip():
            agent = _infer_agent_from_path_text(raw.strip())
            if agent is not None:
                return agent

    nested = data.get("payload")
    if isinstance(nested, dict):
        nested_agent = infer_launch_agent(nested)
        if nested_agent is not None:
            return nested_agent
    return None


def skill_directories_for_payload(data: dict[str, Any]) -> list[Path]:
    """Project-local and user-home skill directories for the active agent."""
    cwd = _payload_cwd(data)
    agent = infer_launch_agent(data)
    pairs = [_AGENT_SKILL_DIRS[agent]] if agent is not None else list(_AGENT_SKILL_DIRS.values())

    directories: list[Path] = []
    seen: set[Path] = set()
    for project_rel, home_rel in pairs:
        for candidate in (cwd / project_rel, Path(home_rel).expanduser()):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            directories.append(candidate)
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

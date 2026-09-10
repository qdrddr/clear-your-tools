"""Hook payload skills supplied by cyt-client."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cyt.launch.upstream import AgentName
from cyt.skills.catalog import SkillEntryRef, build_registry


def client_skills_from_payload(payload: dict[str, Any]) -> list[dict[str, str]] | None:
    """Return parsed client skills when ``cyt_skills`` is present on the hook payload."""
    if "cyt_skills" not in payload:
        return None
    raw = payload.get("cyt_skills")
    if not isinstance(raw, list):
        return []

    skills: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not path.strip():
            continue
        if not isinstance(content, str):
            continue
        skills.append({"path": path.strip(), "content": content})
    return skills


def skill_directories_from_payload(payload: dict[str, Any]) -> list[str] | None:
    """Return client-reported skill directory paths when present on the hook payload."""
    if "cyt_skill_directories" not in payload:
        return None
    raw = payload.get("cyt_skill_directories")
    if not isinstance(raw, list):
        return []
    directories: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            directories.append(item.strip())
    return directories


def client_skills_from_directories(directories: list[str]) -> list[dict[str, str]]:
    """Read skill markdown from absolute directory paths for daemon-side fallback."""
    from cyt.tiers.adapters.skills import is_ephemeral_skill_path

    skills: list[dict[str, str]] = []
    seen_hashes: set[str] = set()

    for raw_dir in directories:
        directory = Path(raw_dir).expanduser()
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            if not path.is_file():
                continue
            resolved = str(path.resolve())
            if is_ephemeral_skill_path(resolved):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            skills.append({"path": resolved, "content": content})
    return skills


def build_registry_for_hook_payload(
    config: dict[str, Any],
    payload: dict[str, Any] | None,
    *,
    agent: AgentName | None = None,
    upstream_kind: str | None = None,
) -> list[SkillEntryRef]:
    """Merge cyt-client skills with configured/workspace directory scans."""
    client_skills = client_skills_from_payload(payload) if payload is not None else None
    if client_skills is not None and not client_skills and payload is not None:
        directories = skill_directories_from_payload(payload)
        if directories:
            client_skills = client_skills_from_directories(directories)
    if client_skills is not None:
        return build_registry(
            config,
            agent=agent,
            upstream_kind=upstream_kind,
            client_skills=client_skills,
        )
    return build_registry(config, agent=agent, upstream_kind=upstream_kind)

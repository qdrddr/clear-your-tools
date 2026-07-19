"""MCPC per-session skills inline registry sources."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.config import (
    load_config,
    mcpc_skills_in_session_enabled,
    skills_enabled,
    uses_mcpc_tool_catalog,
)
from cyt.mcpc.skills_cache import get_mcpc_skills_snapshot
from cyt.skills.catalog import SkillEntryRef, build_registry_from_inline_sources


def session_skill_inline_sources(config: dict[str, Any] | None = None) -> list[dict[str, str]]:
    cfg = config or load_config()
    if not uses_mcpc_tool_catalog(cfg) or not skills_enabled(cfg):
        return []
    if not mcpc_skills_in_session_enabled(cfg):
        return []
    snapshot = get_mcpc_skills_snapshot(cfg, blocking=False)
    if snapshot is None:
        return []
    return list(snapshot.in_session)


def build_session_skill_registry(config: dict[str, Any] | None = None) -> list[SkillEntryRef]:
    cfg = config or load_config()
    sources = session_skill_inline_sources(cfg)
    if not sources:
        return []
    original_by_hash = {source["content_sha256"]: Path(source["path"]) for source in sources}
    return build_registry_from_inline_sources(cfg, sources, original_by_hash=original_by_hash)


def append_mcpc_session_skill_entries(
    entries: list[SkillEntryRef],
    config: dict[str, Any] | None = None,
) -> list[SkillEntryRef]:
    session_entries = build_session_skill_registry(config)
    if not session_entries:
        return list(entries)
    existing = {entry.doc_id for entry in entries}
    merged = list(entries)
    for entry in session_entries:
        if entry.doc_id not in existing:
            merged.append(entry)
            existing.add(entry.doc_id)
    return merged

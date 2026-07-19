"""MCPC ``mcpc help --skill`` inline registry source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.config import load_config, mcpc_skills_own_enabled, skills_enabled, uses_mcpc_tool_catalog
from cyt.mcpc.skills_cache import get_mcpc_skills_snapshot
from cyt.skills.catalog import SkillEntryRef, build_registry_from_inline_sources

HELP_SKILL_PATH = "mcpc/help/SKILL.md"
HELP_SKILL_REGISTRY_PATH = "mcpc/help/SKILL"
HELP_SKILL_COMMAND = "mcpc help --skill"


def help_skill_inline_source(config: dict[str, Any] | None = None) -> dict[str, str] | None:
    cfg = config or load_config()
    if not uses_mcpc_tool_catalog(cfg) or not skills_enabled(cfg):
        return None
    if not mcpc_skills_own_enabled(cfg):
        return None
    snapshot = get_mcpc_skills_snapshot(cfg, blocking=False)
    if snapshot is None or snapshot.own_skill is None:
        return None
    return dict(snapshot.own_skill)


def build_help_skill_registry(config: dict[str, Any] | None = None) -> list[SkillEntryRef]:
    cfg = config or load_config()
    source = help_skill_inline_source(cfg)
    if source is None:
        return []
    content_hash = source["content_sha256"]
    registry_source = {**source, "path": HELP_SKILL_REGISTRY_PATH}
    return build_registry_from_inline_sources(
        cfg,
        [registry_source],
        original_by_hash={content_hash: Path(HELP_SKILL_PATH)},
    )


def append_mcpc_help_skill_entries(
    entries: list[SkillEntryRef],
    config: dict[str, Any] | None = None,
) -> list[SkillEntryRef]:
    help_entries = build_help_skill_registry(config)
    if not help_entries:
        return list(entries)
    existing = {entry.doc_id for entry in entries}
    merged = list(entries)
    for entry in help_entries:
        if entry.doc_id not in existing:
            merged.append(entry)
            existing.add(entry.doc_id)
    return merged

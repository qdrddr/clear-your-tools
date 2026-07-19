"""MCPC per-session resources inline registry sources and match splitting."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from cyt.config import (
    load_config,
    mcpc_resources_enabled,
    skills_enabled,
    uses_mcpc_tool_catalog,
)
from cyt.mcpc.skills_cache import get_mcpc_skills_snapshot
from cyt.resources.inject import MatchedResource
from cyt.skills.catalog import SkillEntryRef, build_registry_from_inline_sources
from cyt.skills.search import MatchedSkill

_RESOURCE_PATH_RE = re.compile(r"/resources/")


def _parsed_frontmatter(markdown: str) -> dict[str, Any]:
    text = markdown.strip()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    yaml_body = text[3:end].strip()
    if not yaml_body:
        return {}
    try:
        parsed = yaml.safe_load(yaml_body)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def session_resource_inline_sources(config: dict[str, Any] | None = None) -> list[dict[str, str]]:
    cfg = config or load_config()
    if not uses_mcpc_tool_catalog(cfg) or not skills_enabled(cfg):
        return []
    if not mcpc_resources_enabled(cfg):
        return []
    snapshot = get_mcpc_skills_snapshot(cfg, blocking=False)
    if snapshot is None:
        return []
    return list(snapshot.resources)


def build_session_resource_registry(config: dict[str, Any] | None = None) -> list[SkillEntryRef]:
    cfg = config or load_config()
    sources = session_resource_inline_sources(cfg)
    if not sources:
        return []
    original_by_hash = {source["content_sha256"]: Path(source["path"]) for source in sources}
    return build_registry_from_inline_sources(cfg, sources, original_by_hash=original_by_hash)


def append_mcpc_session_resource_entries(
    entries: list[SkillEntryRef],
    config: dict[str, Any] | None = None,
) -> list[SkillEntryRef]:
    resource_entries = build_session_resource_registry(config)
    if not resource_entries:
        return list(entries)
    existing = {entry.doc_id for entry in entries}
    merged = list(entries)
    for entry in resource_entries:
        if entry.doc_id not in existing:
            merged.append(entry)
            existing.add(entry.doc_id)
    return merged


def _is_resource_match(match: MatchedSkill) -> bool:
    if _RESOURCE_PATH_RE.search(match.file_path):
        return True
    frontmatter = _parsed_frontmatter(match.markdown)
    return frontmatter.get("mcpc_kind") == "resource"


def split_mcpc_resource_matches(
    matches: Sequence[MatchedSkill | dict[str, Any]],
) -> tuple[list[MatchedSkill], list[MatchedResource]]:
    skill_matches: list[MatchedSkill] = []
    resource_matches: list[MatchedResource] = []
    for match in matches:
        if isinstance(match, dict):
            continue
        if not _is_resource_match(match):
            skill_matches.append(match)
            continue
        frontmatter = _parsed_frontmatter(match.markdown)
        command = str(frontmatter.get("mcpc_command") or "").strip()
        if not command:
            continue
        description = str(frontmatter.get("description") or "").strip()
        resource_matches.append(
            MatchedResource(
                doc_id=match.doc_id,
                file_path=match.file_path,
                markdown=match.markdown,
                name=match.name,
                command=command,
                description=description,
                score=match.score,
                token_count=match.token_count,
            ),
        )
    return skill_matches, resource_matches

"""Executor MCP execute skill as a standard ``MatchedSkill`` for injection."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from cyt.indexer.tokens import count_tokens
from cyt.skills.catalog import SkillEntryRef, build_registry_from_inline_sources
from cyt.skills.search import MatchedSkill

logger = logging.getLogger(__name__)

EXECUTOR_SKILL_NAME = "executor"
EXECUTOR_SKILL_DOC_ID = "executor"
EXECUTOR_SKILL_PATH = "executor/execute"
EXECUTOR_REGISTRY_PATH = "executor/executor"


def execute_skill_text(executor_mcp: dict[str, Any] | None) -> str:
    if not isinstance(executor_mcp, dict):
        return ""
    execute_skill = executor_mcp.get("execute_skill")
    return str(execute_skill).strip() if isinstance(execute_skill, str) else ""


def _executor_registry_content(text: str) -> str:
    body = text.strip()
    if body.startswith("---"):
        return body
    return f"---\nname: {EXECUTOR_SKILL_NAME}\n---\n\n{body}"


def executor_skill_inline_source(config: dict[str, Any] | None = None) -> dict[str, str] | None:
    """Return cached executor execute skill as an inline registry source."""
    from cyt.config import load_config, uses_executor_tool_catalog

    cfg = config or load_config()
    if not uses_executor_tool_catalog(cfg):
        return None
    try:
        from cyt.executor.http import get_executor_mcp_cache

        text = execute_skill_text(get_executor_mcp_cache(cfg, allow_prompt=False))
    except Exception as exc:
        logger.debug("executor skill inline source skipped: %s", exc)
        return None
    if not text:
        return None
    content = _executor_registry_content(text)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "path": EXECUTOR_SKILL_PATH,
        "content": content,
        "content_sha256": content_hash,
    }


def build_executor_skill_registry(config: dict[str, Any] | None = None) -> list[SkillEntryRef]:
    """Decompose the cached executor execute skill into registry entries."""
    from cyt.config import load_config

    cfg = config or load_config()
    source = executor_skill_inline_source(cfg)
    if source is None:
        return []
    content_hash = source["content_sha256"]
    registry_source = {**source, "path": EXECUTOR_REGISTRY_PATH}
    return build_registry_from_inline_sources(
        cfg,
        [registry_source],
        original_by_hash={content_hash: Path(EXECUTOR_SKILL_PATH)},
    )


def append_executor_skill_entries(
    entries: list[SkillEntryRef],
    config: dict[str, Any] | None = None,
) -> list[SkillEntryRef]:
    """Append decomposed executor skill entries when not already present."""
    executor_entries = build_executor_skill_registry(config)
    if not executor_entries:
        return list(entries)
    existing = {entry.doc_id for entry in entries}
    merged = list(entries)
    for entry in executor_entries:
        if entry.doc_id not in existing:
            merged.append(entry)
            existing.add(entry.doc_id)
    return merged


def executor_skill_match_from_text(skill_text: str) -> MatchedSkill | None:
    text = skill_text.strip()
    if not text:
        return None
    return MatchedSkill(
        doc_id=EXECUTOR_SKILL_DOC_ID,
        file_path=EXECUTOR_SKILL_PATH,
        markdown=text,
        name=EXECUTOR_SKILL_NAME,
        score=1.0,
        token_count=count_tokens(text),
    )


def executor_skill_match_from_cache(executor_mcp: dict[str, Any] | None) -> MatchedSkill | None:
    return executor_skill_match_from_text(execute_skill_text(executor_mcp))


def executor_skill_match(config: dict[str, Any] | None = None) -> MatchedSkill | None:
    from cyt.config import load_config, uses_executor_tool_catalog

    cfg = config or load_config()
    if not uses_executor_tool_catalog(cfg):
        return None
    try:
        from cyt.executor.http import get_executor_mcp_cache

        return executor_skill_match_from_cache(
            get_executor_mcp_cache(cfg, allow_prompt=False),
        )
    except Exception as exc:
        logger.debug("executor skill match skipped: %s", exc)
        return None


def with_executor_skill_matches(
    matches: list[MatchedSkill],
    config: dict[str, Any] | None = None,
) -> list[MatchedSkill]:
    """Append the cached executor skill when not already present."""
    executor = executor_skill_match(config)
    if executor is None:
        return list(matches)
    if any(
        match.name == EXECUTOR_SKILL_NAME or match.doc_id == EXECUTOR_SKILL_DOC_ID
        for match in matches
    ):
        return list(matches)
    return [*matches, executor]

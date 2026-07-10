"""Executor MCP execute skill as a standard ``MatchedSkill`` for injection."""

from __future__ import annotations

import logging
from typing import Any

from cyt.indexer.tokens import count_tokens
from cyt.skills.search import MatchedSkill

logger = logging.getLogger(__name__)

EXECUTOR_SKILL_NAME = "executor"
EXECUTOR_SKILL_DOC_ID = "executor"
EXECUTOR_SKILL_PATH = "executor/execute"


def execute_skill_text(executor_mcp: dict[str, Any] | None) -> str:
    if not isinstance(executor_mcp, dict):
        return ""
    execute_skill = executor_mcp.get("execute_skill")
    return str(execute_skill).strip() if isinstance(execute_skill, str) else ""


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
        from cyt.tools.sources.executor_http import get_executor_mcp_cache

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

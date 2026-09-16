"""Tier-scoped skill materialization and token counting for ``tiers stats``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.skills.frontmatter import injection_markdown_body
from cyt.skills.search import MatchedSkill

_carried_token_memo: dict[str, int] = {}


def clear_carried_token_memo() -> None:
    """Reset memo used by :func:`carried_skill_token_count` (tests)."""
    _carried_token_memo.clear()


def load_skill_markdown(entity: dict[str, Any]) -> str | None:
    """Read full SKILL.md content from the entity ``source_path``."""
    source_path = entity.get("source_path")
    if not isinstance(source_path, str) or not source_path.strip():
        return None
    path = Path(source_path).expanduser()
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _matched_skill_from_markdown(
    markdown: str,
    *,
    entity: dict[str, Any] | None = None,
) -> MatchedSkill:
    entity = entity or {}
    name = entity.get("name") or entity.get("display_name")
    file_path = str(entity.get("source_path") or entity.get("entity_id") or "")
    return MatchedSkill(
        doc_id=str(entity.get("doc_id") or entity.get("entity_id") or ""),
        file_path=file_path,
        markdown=markdown,
        name=str(name) if name is not None else None,
        score=1.0,
        token_count=0,
    )


def description_and_headers_markdown(
    markdown: str,
    *,
    entity: dict[str, Any] | None = None,
    min_headers: int = 6,
) -> str:
    """T2 effective view: frontmatter/description plus header lines from the body."""
    from cyt.tiers.adapters.skills import (
        _headers_only_markdown,
        skill_description_only_markdown_from_match,
    )

    match = _matched_skill_from_markdown(markdown, entity=entity)
    description = skill_description_only_markdown_from_match(match).strip()
    body = injection_markdown_body(markdown)
    headers = _headers_only_markdown(body, min_headers=min_headers).strip()
    parts = [part for part in (description, headers) if part]
    return "\n".join(parts)


def markdown_for_tier(
    markdown: str,
    tier_label: str,
    *,
    entity: dict[str, Any] | None = None,
) -> str:
    """Trim *markdown* to the portion materialized at *tier_label*."""
    tier = tier_label.strip().upper()
    if tier == "T0":
        return ""
    if tier == "T1":
        from cyt.tiers.adapters.skills import skill_description_only_markdown_from_match

        return skill_description_only_markdown_from_match(
            _matched_skill_from_markdown(markdown, entity=entity),
        )
    if tier == "T2":
        return description_and_headers_markdown(markdown, entity=entity)
    return markdown


def carried_skill_token_count(entity: dict[str, Any]) -> int | None:
    """Full-skill (T4) token count for *entity*, memoized by entity id."""
    from cyt.indexer.tokens import count_tokens

    entity_id = str(entity.get("entity_id") or "").strip()

    markdown = load_skill_markdown(entity)
    if markdown is None:
        return None

    try:
        count = count_tokens(markdown)
    except Exception:
        body = markdown.strip()
        count = len(body.split()) if body else 0

    if entity_id:
        prior = _carried_token_memo.get(entity_id)
        count = max(prior or 0, count)
        _carried_token_memo[entity_id] = count
    return count


def effective_skill_token_count(entity: dict[str, Any], tier_label: str) -> int | None:
    """Token count for *entity* materialized at *tier_label*."""
    tier = tier_label.strip().upper()
    if tier == "T0":
        return 0

    carried = carried_skill_token_count(entity)
    if carried is None:
        return None

    markdown = load_skill_markdown(entity)
    if markdown is None:
        if tier in {"T3", "T4"}:
            return carried
        return None

    trimmed = markdown_for_tier(markdown, tier, entity=entity)
    if not trimmed.strip():
        return 0

    from cyt.indexer.tokens import count_tokens

    try:
        effective = count_tokens(trimmed)
    except Exception:
        effective = len(trimmed.split())

    if tier in {"T1", "T2", "T3", "T4"}:
        return min(effective, carried)
    return effective


def attach_skill_token_count(record: dict[str, Any]) -> None:
    """Set ``token_count`` on a skill status record when source content exists."""
    count = carried_skill_token_count(record)
    if count is not None:
        record["token_count"] = count

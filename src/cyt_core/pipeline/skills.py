"""Composite skills search pipeline."""

from __future__ import annotations

from typing import Any

from cyt_core.indexer.pipeline import search_skills_and_select

__all__ = ["search_skills_for_injection"]


def search_skills_for_injection(
    entries: list[dict[str, Any]],
    query: str,
    *,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """BM25 skill search with optional frontmatter gate and greedy budget selection."""
    return search_skills_and_select(entries, query, options=options)

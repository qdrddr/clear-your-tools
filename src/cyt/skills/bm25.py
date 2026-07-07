"""BM25 selection over cached skill chunks via cyt-indexer-sdk."""

from __future__ import annotations

import logging
from typing import Any

from cyt_indexer.bm25_search import bm25_frontmatter_gate, bm25_search_skill_chunks

from cyt.common.paths import shorten_home_path
from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.config import bm25_score_skills, skills_frontmatter_upper_limit
from cyt.pruners.bm25 import bm25_stage_usage
from cyt.skills.catalog import SkillEntryRef
from cyt.skills.diagnostics import FrontmatterGateRow, SearchItemRow
from cyt.skills.frontmatter import frontmatter_search_text
from cyt.skills.search import MatchedSkill

logger = logging.getLogger(__name__)


def _entries_payload(entries: list[SkillEntryRef]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for entry in entries:
        raw = entry.document.get("frontmatter")
        frontmatter = raw if isinstance(raw, str) else None
        payload.append(
            {
                "entry_dir": entry.entry_dir,
                "doc_id": entry.doc_id,
                "source_path": shorten_home_path(entry.source_path),
                "frontmatter": frontmatter_search_text(frontmatter) or None,
                "cache_key": entry.cache_key,
                "bm25_chunk_dir": entry.bm25_chunk_dir,
            },
        )
    return payload


def frontmatter_gate_trace(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[FrontmatterGateRow], float]:
    """Return BM25 frontmatter scores for every skill and the exclusion upper limit."""
    upper = skills_frontmatter_upper_limit(config)
    if not query.strip() or not entries:
        return [], upper

    result = bm25_frontmatter_gate(_entries_payload(entries), query, upper_limit=upper)
    trace = result.get("trace") if isinstance(result, dict) else {}
    trace_rows = trace.get("rows") if isinstance(trace, dict) else []
    rows: list[FrontmatterGateRow] = []
    seen: set[tuple[str, str]] = set()

    entries_by_doc_id = {entry.doc_id: entry for entry in entries}
    if isinstance(trace_rows, list):
        for row in trace_rows:
            if not isinstance(row, dict):
                continue
            entry_dir = str(row.get("entry_dir", ""))
            doc_id = str(row.get("doc_id", ""))
            key = (entry_dir, doc_id)
            seen.add(key)
            score_val = row.get("score")
            normalized_score = float(score_val) if score_val is not None else None
            entry = entries_by_doc_id.get(doc_id)
            file_path = shorten_home_path(entry.source_path) if entry is not None else doc_id
            rows.append(
                FrontmatterGateRow(
                    entry_dir=entry_dir,
                    doc_id=doc_id,
                    file_path=file_path,
                    score=normalized_score,
                    raw_score=normalized_score,
                    passed=bool(row.get("passed", True)),
                ),
            )

    for entry in entries:
        key = (entry.entry_dir, entry.doc_id)
        if key in seen:
            continue
        rows.append(
            FrontmatterGateRow(
                entry_dir=entry.entry_dir,
                doc_id=entry.doc_id,
                file_path=shorten_home_path(entry.source_path),
                score=None,
                raw_score=None,
                passed=True,
            ),
        )
    return rows, upper


def excluded_by_frontmatter_gate(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> set[tuple[str, str]]:
    rows, upper = frontmatter_gate_trace(query, entries, config=config)
    excluded: set[tuple[str, str]] = set()
    for row in rows:
        if row.passed:
            continue
        excluded.add((row.entry_dir, row.doc_id))
        logger.debug(
            "skills frontmatter gate excluded doc_id=%s score=%.4f limit=%.4f",
            row.doc_id,
            row.score if row.score is not None else 0.0,
            upper,
        )
    return excluded


def bm25_skill_chunks(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[MatchedSkill], StageTokenUsage]:
    """Select relevant skill chunks via BM25 and reconstruct matched skills."""
    matches, _rows, _threshold, usage = bm25_skill_chunks_with_trace(
        query,
        entries,
        config=config,
    )
    return matches, usage


def _match_from_native(item: dict[str, Any]) -> MatchedSkill:
    return MatchedSkill(
        doc_id=str(item.get("doc_id", "")),
        file_path=str(item.get("file_path", "")),
        markdown=str(item.get("markdown", "")),
        name=item.get("name") if item.get("name") is not None else None,
        score=float(item.get("score", 0)),
        token_count=int(item.get("token_count", 0)),
    )


def bm25_skill_chunks_with_trace(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[MatchedSkill], list[SearchItemRow], float, StageTokenUsage]:
    """Select skill chunks via BM25 and return per-chunk scores."""
    threshold = bm25_score_skills(config)
    if not query.strip() or not entries:
        return [], [], threshold, empty_usage()

    result = bm25_search_skill_chunks(
        _entries_payload(entries),
        query,
        threshold=threshold,
        excluded=None,
    )

    search_rows: list[SearchItemRow] = []
    for row in result.get("trace_rows", []) if isinstance(result, dict) else []:
        if not isinstance(row, dict):
            continue
        search_rows.append(
            SearchItemRow(
                file_path=str(row.get("file_path", "")),
                doc_id=str(row.get("doc_id", "")),
                item_id=str(row.get("item_id", "")),
                item_kind="chunk",
                score=float(row.get("score", 0)),
                passed=bool(row.get("passed", False)),
            ),
        )

    matches: list[MatchedSkill] = []
    for item in result.get("matches", []) if isinstance(result, dict) else []:
        if isinstance(item, dict):
            matches.append(_match_from_native(item))

    return matches, search_rows, threshold, bm25_stage_usage()


__all__ = [
    "bm25_skill_chunks",
    "bm25_skill_chunks_with_trace",
    "excluded_by_frontmatter_gate",
    "frontmatter_gate_trace",
]

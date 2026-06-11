"""BM25 selection over cached skill chunks (not decomposed nodes)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from cyt_indexer import load_skills_index_from_dir, reconstruct_skill_markdown

from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.config import bm25_score_skills, skills_frontmatter_upper_limit
from cyt.indexer.tokens import count_tokens
from cyt.pruners.bm25 import bm25_catalog_dict
from cyt.skills.budget import cap_matched_skills_by_tokens
from cyt.skills.catalog import SkillEntryRef, _iter_content_chunk_ids, _shorten_home_path
from cyt.skills.frontmatter import frontmatter_search_text, skill_name_from_frontmatter
from cyt.skills.search import MatchedSkill, _frontmatter_by_doc

logger = logging.getLogger(__name__)


def _strip_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content
    end = content.find("\n---", 3)
    if end == -1:
        return content
    return content[end + 4 :].lstrip("\n")


def _build_frontmatter_corpus(entries: list[SkillEntryRef]) -> dict[str, Any]:
    md_items: list[dict[str, Any]] = []
    for entry in entries:
        raw = entry.document.get("frontmatter")
        frontmatter = raw if isinstance(raw, str) else None
        content = frontmatter_search_text(frontmatter)
        if not content.strip():
            continue
        file_path = _shorten_home_path(entry.source_path)
        md_items.append(
            {
                "id": entry.doc_id,
                "doc_id": entry.doc_id,
                "file_path": file_path,
                "content": content,
                "score": 0.0,
                "start_line": 1,
                "end_line": 1,
                "language": "markdown",
                "cache_key": entry.cache_key,
                "entry_dir": entry.entry_dir,
            },
        )
    return {"md": md_items}


def excluded_by_frontmatter_gate(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> set[tuple[str, str]]:
    corpus = _build_frontmatter_corpus(entries)
    md_items = corpus.get("md")
    if not isinstance(md_items, list) or not md_items:
        return set()

    scored, _usage = bm25_catalog_dict(corpus, query, prune=False, config=config)
    upper = skills_frontmatter_upper_limit(config)
    excluded: set[tuple[str, str]] = set()
    for item in scored.get("md", []):
        if not isinstance(item, dict):
            continue
        score = float(item.get("score", 0))
        if score < upper:
            continue
        entry_dir = str(item.get("entry_dir", ""))
        doc_id = str(item.get("doc_id", ""))
        if not entry_dir or not doc_id:
            continue
        excluded.add((entry_dir, doc_id))
        logger.debug(
            "skills frontmatter gate excluded doc_id=%s score=%.4f limit=%.4f",
            doc_id,
            score,
            upper,
        )
    return excluded


def _build_corpus(entries: list[SkillEntryRef]) -> dict[str, Any]:
    md_items: list[dict[str, Any]] = []
    for entry in entries:
        doc_dir = Path(entry.entry_dir) / "skills" / "decomposed" / entry.doc_id
        structure = entry.document.get("structure")
        if not structure:
            continue
        file_path = _shorten_home_path(entry.source_path)
        for chunk_id in _iter_content_chunk_ids(structure):
            chunk_path = doc_dir / "chunks" / f"{chunk_id}.md"
            if not chunk_path.is_file():
                continue
            content = _strip_frontmatter(chunk_path.read_text(encoding="utf-8"))
            if not content.strip():
                continue
            md_items.append(
                {
                    "id": str(chunk_id),
                    "doc_id": entry.doc_id,
                    "file_path": file_path,
                    "content": content,
                    "score": 0.0,
                    "start_line": 1,
                    "end_line": 1,
                    "language": "markdown",
                    "cache_key": entry.cache_key,
                    "entry_dir": entry.entry_dir,
                },
            )
    return {"md": md_items}


def _reconstruct_for_doc(
    entry_dir: str,
    doc_id: str,
    chunk_ids: list[int],
) -> str:
    index = load_skills_index_from_dir(entry_dir)
    chunk_specs = [str(chunk_id) for chunk_id in sorted(chunk_ids)]
    result = reconstruct_skill_markdown(
        index,
        doc_id,
        chunk_id_specs=chunk_specs,
    )
    return str(result.get("markdown", ""))


def _group_survivors_by_doc(
    survivors: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for item in survivors:
        doc_id = str(item.get("doc_id", ""))
        if not doc_id:
            continue
        by_doc.setdefault(doc_id, []).append(item)
    return by_doc


def reconstruct_skills_from_bm25_items(
    survivors: list[dict[str, Any]],
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> list[MatchedSkill]:
    """Group surviving chunk items by doc and rebuild MatchedSkill list."""
    by_doc = _group_survivors_by_doc(survivors)
    if not by_doc:
        return []

    frontmatter_by_doc = _frontmatter_by_doc(entries)
    matched: list[MatchedSkill] = []
    for doc_id, items in by_doc.items():
        items.sort(key=lambda row: float(row.get("score", 0)), reverse=True)
        entry_dir = str(items[0].get("entry_dir", ""))
        file_path = str(items[0].get("file_path", ""))
        top_score = float(items[0].get("score", 0))
        chunk_ids = [int(item["id"]) for item in items if item.get("id") is not None]
        markdown = _reconstruct_for_doc(entry_dir, doc_id, chunk_ids)
        if not markdown.strip():
            continue
        token_count = count_tokens(markdown)
        frontmatter = frontmatter_by_doc.get((entry_dir, doc_id))
        name = skill_name_from_frontmatter(frontmatter)
        matched.append(
            MatchedSkill(
                doc_id=doc_id,
                file_path=file_path,
                markdown=markdown,
                name=name,
                score=top_score,
                token_count=token_count,
            ),
        )

    return cap_matched_skills_by_tokens(
        matched,
        config=config,
        sort_key=lambda row: row.score,
        reverse=True,
    )


def bm25_skill_chunks(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[MatchedSkill], StageTokenUsage]:
    """Select relevant skill chunks via BM25 and reconstruct matched skills."""
    if not query.strip() or not entries:
        return [], empty_usage()

    corpus = _build_corpus(entries)
    md_items = corpus.get("md")
    if not isinstance(md_items, list) or not md_items:
        return [], empty_usage()

    scored, usage = bm25_catalog_dict(corpus, query, prune=False, config=config)
    threshold = bm25_score_skills(config)
    survivors = [
        item
        for item in scored.get("md", [])
        if isinstance(item, dict) and float(item.get("score", 0)) >= threshold
    ]
    if not survivors:
        return [], usage

    matches = reconstruct_skills_from_bm25_items(survivors, entries, config=config)
    return matches, usage

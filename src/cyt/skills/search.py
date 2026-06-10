"""BM25 search over cached skill chunks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt_indexer import load_skills_index_from_dir, reconstruct_skill_markdown

from cyt.config import bm25_score_skills, skills_max_tokens_per_request
from cyt.indexer.tokens import count_tokens
from cyt.pruners.bm25 import bm25_catalog_dict
from cyt.skills.catalog import SkillEntryRef, _iter_chunk_ids

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchedSkill:
    doc_id: str
    file_path: str
    markdown: str
    score: float
    token_count: int


def _strip_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content
    end = content.find("\n---", 3)
    if end == -1:
        return content
    return content[end + 4 :].lstrip("\n")


def _build_corpus(entries: list[SkillEntryRef]) -> dict[str, Any]:
    md_items: list[dict[str, Any]] = []
    for entry in entries:
        doc_dir = Path(entry.entry_dir) / "skills" / "decomposed" / entry.doc_id
        structure = entry.document.get("structure")
        if not structure:
            continue
        file_path = str(entry.document.get("path", entry.source_path))
        for chunk_id in _iter_chunk_ids(structure):
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


def search_skills(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> list[MatchedSkill]:
    """Return matched skill reconstructions sorted by score (highest first)."""
    if not query.strip() or not entries:
        return []

    corpus = _build_corpus(entries)
    md_items = corpus.get("md")
    if not isinstance(md_items, list) or not md_items:
        return []

    scored, _usage = bm25_catalog_dict(corpus, query, prune=False, config=config)
    threshold = bm25_score_skills(config)
    survivors = [
        item
        for item in scored.get("md", [])
        if isinstance(item, dict) and float(item.get("score", 0)) >= threshold
    ]
    if not survivors:
        return []

    by_doc: dict[str, list[dict[str, Any]]] = {}
    for item in survivors:
        doc_id = str(item.get("doc_id", ""))
        if not doc_id:
            continue
        by_doc.setdefault(doc_id, []).append(item)

    max_tokens = skills_max_tokens_per_request(config)
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
        matched.append(
            MatchedSkill(
                doc_id=doc_id,
                file_path=file_path,
                markdown=markdown,
                score=top_score,
                token_count=token_count,
            ),
        )

    matched.sort(key=lambda row: row.score, reverse=True)

    total_tokens = sum(item.token_count for item in matched)
    while matched and total_tokens > max_tokens:
        dropped = matched.pop()
        total_tokens -= dropped.token_count

    return matched

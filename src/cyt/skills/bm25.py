"""BM25 selection over cached skill chunks (not decomposed nodes)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import numpy as np
from bm25s.tokenization import Tokenizer
from cyt_indexer import load_skills_index_from_dir, reconstruct_skill_markdown

from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.config import bm25_score_skills, skills_frontmatter_upper_limit
from cyt.indexer.tokens import count_tokens
from cyt.pruners.bm25 import (
    Bm25Index,
    _query_token_ids,
    bm25_catalog_dict,
    build_or_load_index,
    normalize_bm25_similarity,
)
from cyt.skills.catalog import SkillEntryRef, _iter_content_chunk_ids, _shorten_home_path
from cyt.skills.diagnostics import FrontmatterGateRow, FrontmatterTokenContribution, SearchItemRow
from cyt.skills.frontmatter import frontmatter_search_text, skill_name_from_frontmatter
from cyt.skills.search import MatchedSkill, _frontmatter_by_doc

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenized_string_stems(tokenizer: Tokenizer, text: str) -> list[str]:
    tokenized = tokenizer.tokenize(
        [text],
        update_vocab=False,
        return_as="string",
        show_progress=False,
    )
    if not isinstance(tokenized, list) or not tokenized:
        return []
    stems = tokenized[0]
    if not isinstance(stems, list):
        return []
    return [stem for stem in stems if isinstance(stem, str)]


def _strip_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content
    end = content.find("\n---", 3)
    if end == -1:
        return content
    body_start = end + 4
    return content[body_start:].lstrip("\n")


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


def _surface_forms_for_stem(text: str, stem: str, tokenizer: Tokenizer) -> tuple[str, ...]:
    forms: list[str] = []
    seen: set[str] = set()
    for word in _WORD_RE.findall(text):
        stems = _tokenized_string_stems(tokenizer, word)
        if not stems or stems[0] != stem:
            continue
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        forms.append(word)
    return tuple(forms)


def _frontmatter_doc_key_maps(
    md_items: list[dict[str, Any]],
    doc_mapping: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], str]]:
    key_to_doc_idx: dict[tuple[str, str], int] = {}
    key_to_content: dict[tuple[str, str], str] = {}
    for mapping_idx, mapping in enumerate(doc_mapping):
        if mapping.get("list_key") != "md":
            continue
        item_index = mapping.get("item_index")
        if not isinstance(item_index, int) or item_index >= len(md_items):
            continue
        item = md_items[item_index]
        entry_dir = str(item.get("entry_dir", ""))
        doc_id = str(item.get("doc_id", ""))
        if not entry_dir or not doc_id:
            continue
        key = (entry_dir, doc_id)
        key_to_doc_idx[key] = mapping_idx
        key_to_content[key] = str(item.get("content", ""))
    return key_to_doc_idx, key_to_content


def _query_stem_scores(index: Bm25Index, query: str) -> dict[str, np.ndarray]:
    unique_stems = list(dict.fromkeys(_tokenized_string_stems(index.tokenizer, query)))
    stem_scores: dict[str, np.ndarray] = {}
    for stem in unique_stems:
        token_ids = _query_token_ids(index.tokenizer, stem)
        if not token_ids:
            continue
        scores = np.asarray(index.retriever.get_scores_from_ids(token_ids), dtype=float).reshape(-1)
        if scores.size:
            stem_scores[stem] = scores
    return stem_scores


def _frontmatter_token_contributions(
    doc_idx: int,
    content: str,
    query: str,
    stem_scores: dict[str, np.ndarray],
    tokenizer: Tokenizer,
) -> tuple[FrontmatterTokenContribution, ...]:
    rows: list[FrontmatterTokenContribution] = []
    for stem, scores in stem_scores.items():
        if doc_idx >= scores.size:
            continue
        score = float(scores[doc_idx])
        if score <= 0.0:
            continue
        rows.append(
            FrontmatterTokenContribution(
                stem=stem,
                score=normalize_bm25_similarity(score),
                query_terms=_surface_forms_for_stem(query, stem, tokenizer),
                frontmatter_terms=_surface_forms_for_stem(content, stem, tokenizer),
            ),
        )
    rows.sort(key=lambda row: (-row.score, row.stem))
    return tuple(rows)


def _frontmatter_bm25_by_key(
    query: str,
    md_items: list[dict[str, Any]],
    corpus: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[tuple[str, str], tuple[float, float, tuple[FrontmatterTokenContribution, ...]]]:
    """Return raw score, normalized score, and token contributions per skill."""
    if not query.strip() or not md_items:
        return {}

    index = build_or_load_index(corpus, config=config)
    if index is None:
        return {}

    key_to_doc_idx, key_to_content = _frontmatter_doc_key_maps(md_items, index.doc_mapping)

    query_token_ids = _query_token_ids(index.tokenizer, query)
    if not query_token_ids:
        return {}

    raw_scores = np.asarray(
        index.retriever.get_scores_from_ids(query_token_ids),
        dtype=float,
    ).reshape(-1)

    stem_scores = _query_stem_scores(index, query)

    by_key: dict[
        tuple[str, str],
        tuple[float, float, tuple[FrontmatterTokenContribution, ...]],
    ] = {}
    for key, doc_idx in key_to_doc_idx.items():
        if doc_idx >= raw_scores.size:
            continue
        content = key_to_content[key]
        contributions = _frontmatter_token_contributions(
            doc_idx,
            content,
            query,
            stem_scores,
            index.tokenizer,
        )
        by_key[key] = (
            float(raw_scores[doc_idx]),
            normalize_bm25_similarity(float(raw_scores[doc_idx])),
            contributions,
        )
    return by_key


def frontmatter_gate_trace(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[FrontmatterGateRow], float]:
    """Return BM25 frontmatter scores for every skill and the exclusion upper limit."""
    upper = skills_frontmatter_upper_limit(config)
    corpus = _build_frontmatter_corpus(entries)
    md_items = corpus.get("md")
    bm25_by_key: dict[
        tuple[str, str],
        tuple[float, float, tuple[FrontmatterTokenContribution, ...]],
    ] = {}
    if isinstance(md_items, list) and md_items:
        bm25_by_key = _frontmatter_bm25_by_key(query, md_items, corpus, config=config)

    rows: list[FrontmatterGateRow] = []
    for entry in entries:
        key = (entry.entry_dir, entry.doc_id)
        trace = bm25_by_key.get(key)
        raw_score = trace[0] if trace else None
        normalized_score = trace[1] if trace else None
        contributions = trace[2] if trace else ()
        rows.append(
            FrontmatterGateRow(
                entry_dir=entry.entry_dir,
                doc_id=entry.doc_id,
                file_path=_shorten_home_path(entry.source_path),
                score=normalized_score,
                raw_score=raw_score,
                passed=normalized_score is None or normalized_score < upper,
                contributions=contributions,
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


def _matched_skill_for_survivor_items(
    items: list[dict[str, Any]],
    *,
    entry_dir: str,
    doc_id: str,
    frontmatter: str | None,
) -> MatchedSkill | None:
    """Rebuild one skill from all BM25 chunk survivors for that doc."""
    ordered = sorted(items, key=lambda row: float(row.get("score", 0)), reverse=True)
    if not ordered:
        return None

    file_path = str(ordered[0].get("file_path", ""))
    top_score = float(ordered[0].get("score", 0))
    name = skill_name_from_frontmatter(frontmatter)
    chunk_ids = [int(item["id"]) for item in ordered if item.get("id") is not None]
    markdown = _reconstruct_for_doc(entry_dir, doc_id, chunk_ids)
    if not markdown.strip():
        return None
    return MatchedSkill(
        doc_id=doc_id,
        file_path=file_path,
        markdown=markdown,
        name=name,
        score=top_score,
        token_count=count_tokens(markdown),
    )


def reconstruct_skills_from_bm25_items(
    survivors: list[dict[str, Any]],
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> list[MatchedSkill]:
    """Group surviving chunk items by doc and rebuild MatchedSkill list."""
    del config  # reserved for future reconstruction policy hooks
    by_doc = _group_survivors_by_doc(survivors)
    if not by_doc:
        return []

    frontmatter_by_doc = _frontmatter_by_doc(entries)
    matched: list[MatchedSkill] = []
    for doc_id, items in by_doc.items():
        entry_dir = str(items[0].get("entry_dir", ""))
        frontmatter = frontmatter_by_doc.get((entry_dir, doc_id))
        match = _matched_skill_for_survivor_items(
            items,
            entry_dir=entry_dir,
            doc_id=doc_id,
            frontmatter=frontmatter,
        )
        if match is not None:
            matched.append(match)

    return matched


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


def bm25_skill_chunks_with_trace(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[MatchedSkill], list[SearchItemRow], float, StageTokenUsage]:
    """Select skill chunks via BM25 and return per-chunk scores."""
    if not query.strip() or not entries:
        return [], [], bm25_score_skills(config), empty_usage()

    corpus = _build_corpus(entries)
    md_items = corpus.get("md")
    if not isinstance(md_items, list) or not md_items:
        return [], [], bm25_score_skills(config), empty_usage()

    scored, usage = bm25_catalog_dict(corpus, query, prune=False, config=config)
    threshold = bm25_score_skills(config)
    search_rows: list[SearchItemRow] = []
    survivors: list[dict[str, Any]] = []
    for item in scored.get("md", []):
        if not isinstance(item, dict):
            continue
        score = float(item.get("score", 0))
        passed = score >= threshold
        search_rows.append(
            SearchItemRow(
                file_path=str(item.get("file_path", "")),
                doc_id=str(item.get("doc_id", "")),
                item_id=str(item.get("id", "")),
                item_kind="chunk",
                score=score,
                passed=passed,
            ),
        )
        if passed:
            survivors.append(item)

    if not survivors:
        return [], search_rows, threshold, usage

    matches = reconstruct_skills_from_bm25_items(
        survivors,
        entries,
        config=config,
    )
    return matches, search_rows, threshold, usage

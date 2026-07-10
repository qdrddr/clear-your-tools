"""Shared skill match reconstruction from grouped survivors."""

from __future__ import annotations

from typing import Any

from cyt.common.paths import shorten_home_path
from cyt.indexer.bm25_search import batch_reconstruct_skill_matches
from cyt.indexer.pageindex import reconstruct_skill_markdown
from cyt.indexer.tokens import count_tokens_batch
from cyt.skills.catalog import SkillEntryRef, load_entry_skills_index
from cyt.skills.diagnostics import SearchItemRow
from cyt.skills.frontmatter import skill_name_from_frontmatter
from cyt.skills.search import MatchedSkill, _frontmatter_by_doc


class EntryIndexCache:
    """Process-local cache for load_entry_skills_index within one reconstruct pass."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}

    def get(self, entry: SkillEntryRef) -> dict[str, Any]:
        key = (entry.entry_dir, entry.doc_id)
        cached = self._cache.get(key)
        if cached is None:
            cached = load_entry_skills_index(entry)
            self._cache[key] = cached
        return cached


EntryLookup = dict[tuple[str, str], SkillEntryRef]


def build_entry_lookup(entries: list[SkillEntryRef]) -> EntryLookup:
    """Build O(1) lookup keyed by (doc_id, file_path) with doc_id-only fallback."""
    lookup: EntryLookup = {}
    by_doc_id: dict[str, SkillEntryRef] = {}
    for entry in entries:
        file_path = shorten_home_path(entry.source_path)
        lookup[(entry.doc_id, file_path)] = entry
        by_doc_id.setdefault(entry.doc_id, entry)
    for doc_id, entry in by_doc_id.items():
        lookup.setdefault((doc_id, ""), entry)
    return lookup


def entry_for_row(
    row: SearchItemRow,
    entries: list[SkillEntryRef],
    *,
    lookup: EntryLookup | None = None,
) -> SkillEntryRef | None:
    if lookup is not None:
        matched = lookup.get((row.doc_id, row.file_path))
        if matched is not None:
            return matched
        return lookup.get((row.doc_id, ""))

    for entry in entries:
        if entry.doc_id == row.doc_id and shorten_home_path(entry.source_path) == row.file_path:
            return entry
    for entry in entries:
        if entry.doc_id == row.doc_id:
            return entry
    return None


def reconstruct_doc_match(
    entry: SkillEntryRef,
    *,
    id_specs: list[str],
    item_kind: str,
    file_path: str,
    top_score: float,
    frontmatter: str | None,
    index_cache: EntryIndexCache | None = None,
) -> MatchedSkill | None:
    """Reconstruct one skill document from node or chunk id specs."""
    cache = index_cache or EntryIndexCache()
    index = cache.get(entry)
    if item_kind == "chunk":
        result = reconstruct_skill_markdown(
            index,
            entry.doc_id,
            chunk_id_specs=id_specs,
        )
    else:
        result = reconstruct_skill_markdown(
            index,
            entry.doc_id,
            node_id_specs=id_specs,
        )
    markdown = str(result.get("markdown", "")).strip()
    if not markdown:
        return None
    name = skill_name_from_frontmatter(frontmatter)
    return MatchedSkill(
        doc_id=entry.doc_id,
        file_path=file_path,
        markdown=markdown,
        name=name,
        score=top_score,
        token_count=0,
    )


def _matched_skills_from_batch(
    batch_results: list[dict[str, Any]],
) -> list[MatchedSkill]:
    pending: list[tuple[MatchedSkill, str]] = []
    matched: list[MatchedSkill] = []
    for item in batch_results:
        markdown = str(item.get("markdown", "")).strip()
        if not markdown:
            continue
        name = item.get("name")
        raw_token_count = item.get("token_count")
        cached_token_count: int | None = None
        if raw_token_count is not None:
            try:
                parsed = int(raw_token_count)
            except (TypeError, ValueError):
                parsed = 0
            if parsed > 0:
                cached_token_count = parsed
        base_match = MatchedSkill(
            doc_id=str(item.get("doc_id", "")),
            file_path=str(item.get("file_path", "")),
            markdown=markdown,
            name=name if isinstance(name, str) else None,
            score=float(item.get("score", 0)),
            token_count=cached_token_count or 0,
        )
        if cached_token_count is not None:
            matched.append(base_match)
            continue
        pending.append((base_match, markdown))

    if pending:
        token_counts = count_tokens_batch([markdown for _, markdown in pending])
        for (match, _), token_count in zip(pending, token_counts, strict=True):
            matched.append(
                MatchedSkill(
                    doc_id=match.doc_id,
                    file_path=match.file_path,
                    markdown=match.markdown,
                    name=match.name,
                    score=match.score,
                    token_count=token_count,
                ),
            )

    matched.sort(key=lambda item: item.score, reverse=True)
    return matched


def reconstruct_matches_from_items(
    items: list[SearchItemRow],
    entries: list[SkillEntryRef],
    *,
    item_kind: str,
    index_cache: EntryIndexCache | None = None,
) -> list[MatchedSkill]:
    """Group search rows by doc and rebuild MatchedSkill list."""
    lookup = build_entry_lookup(entries)
    by_doc: dict[tuple[str, str], tuple[SkillEntryRef, list[SearchItemRow]]] = {}
    for row in items:
        entry = entry_for_row(row, entries, lookup=lookup)
        if entry is None:
            continue
        key = (entry.entry_dir, entry.doc_id)
        if key not in by_doc:
            by_doc[key] = (entry, [])
        by_doc[key][1].append(row)

    if not by_doc:
        return []

    frontmatter_by_doc = _frontmatter_by_doc(entries)
    groups: list[dict[str, Any]] = []
    for (entry_dir, doc_id), (entry, rows) in by_doc.items():
        top_score = max(row.score for row in rows)
        file_path = rows[0].file_path
        frontmatter = frontmatter_by_doc.get((entry_dir, doc_id))
        id_specs = sorted({row.item_id for row in rows}, key=lambda item_id: int(item_id))
        groups.append(
            {
                "entry_dir": entry.entry_dir,
                "doc_id": entry.doc_id,
                "bm25_chunk_dir": entry.bm25_chunk_dir,
                "item_kind": item_kind,
                "file_path": file_path,
                "score": top_score,
                "frontmatter": frontmatter,
                "id_specs": id_specs,
            },
        )

    return _matched_skills_from_batch(batch_reconstruct_skill_matches(groups))


def reconstruct_matches_from_survivor_dicts(
    survivors: list[dict[str, Any]],
    entries: list[SkillEntryRef],
    *,
    item_kind: str,
    id_field: str,
    index_cache: EntryIndexCache | None = None,
) -> list[MatchedSkill]:
    """Group survivor dicts by doc and rebuild MatchedSkill list."""
    del index_cache  # batch path loads indexes in Rust
    by_doc: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in survivors:
        entry_dir = str(item.get("entry_dir", ""))
        doc_id = str(item.get("doc_id", ""))
        if not entry_dir or not doc_id:
            continue
        by_doc.setdefault((entry_dir, doc_id), []).append(item)

    if not by_doc:
        return []

    frontmatter_by_doc = _frontmatter_by_doc(entries)
    entry_by_key = {(entry.entry_dir, entry.doc_id): entry for entry in entries}
    groups: list[dict[str, Any]] = []

    for (entry_dir, doc_id), items in by_doc.items():
        entry = entry_by_key.get((entry_dir, doc_id))
        if entry is None:
            continue
        items.sort(key=lambda row: float(row.get("score", 0)), reverse=True)
        top_score = float(items[0].get("score", 0))
        file_path = str(items[0].get("file_path", ""))
        id_specs = sorted(
            {str(item[id_field]) for item in items if item.get(id_field) is not None},
            key=lambda item_id: int(item_id),
        )
        frontmatter = frontmatter_by_doc.get((entry_dir, doc_id))
        groups.append(
            {
                "entry_dir": entry.entry_dir,
                "doc_id": entry.doc_id,
                "bm25_chunk_dir": entry.bm25_chunk_dir,
                "item_kind": item_kind,
                "file_path": file_path,
                "score": top_score,
                "frontmatter": frontmatter,
                "id_specs": id_specs,
            },
        )

    return _matched_skills_from_batch(batch_reconstruct_skill_matches(groups))

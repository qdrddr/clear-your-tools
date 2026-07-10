"""Greedy skills selection within an injection token budget."""

from __future__ import annotations

import logging
import os
from typing import Any

from cyt.skills.catalog import SkillEntryRef
from cyt.skills.diagnostics import BudgetItemRow, SearchItemRow
from cyt.skills.reconstruct import (
    EntryIndexCache,
    build_entry_lookup,
    entry_for_row,
    reconstruct_doc_match,
    reconstruct_matches_from_items,
)
from cyt.skills.search import MatchedSkill, _frontmatter_by_doc

logger = logging.getLogger(__name__)


def _native_fallback_enabled() -> bool:
    return os.environ.get("CYT_DEBUG_NATIVE_FALLBACK") == "1"


def _item_key(row: SearchItemRow) -> tuple[str, str]:
    return (row.file_path, row.item_id)


def _wrapped_injection_tokens(matches: list[MatchedSkill]) -> int:
    from cyt.skills.inject import format_agent_skills, injection_token_count

    injected = format_agent_skills(matches)
    if not injected:
        return 0
    return injection_token_count(injected)


def _dedupe_survivors(rows: list[SearchItemRow]) -> list[SearchItemRow]:
    best_by_key: dict[tuple[str, str], SearchItemRow] = {}
    for row in rows:
        key = _item_key(row)
        existing = best_by_key.get(key)
        if existing is None or row.score > existing.score:
            best_by_key[key] = row
    return sorted(
        best_by_key.values(),
        key=lambda item: (-item.score, item.file_path, item.item_id),
    )


def _survivor_payload(
    row: SearchItemRow,
    entry: SkillEntryRef,
    *,
    item_kind: str,
) -> dict[str, str | float | bool | None]:
    raw = entry.document.get("frontmatter")
    frontmatter = raw if isinstance(raw, str) else None
    return {
        "entry_dir": entry.entry_dir,
        "doc_id": entry.doc_id,
        "file_path": row.file_path,
        "item_id": row.item_id,
        "item_kind": item_kind,
        "score": row.score,
        "passed": row.passed,
        "bm25_chunk_dir": entry.bm25_chunk_dir,
        "frontmatter": frontmatter,
    }


def _match_from_native(item: dict[str, Any]) -> MatchedSkill:
    return MatchedSkill(
        doc_id=str(item.get("doc_id", "")),
        file_path=str(item.get("file_path", "")),
        markdown=str(item.get("markdown", "")),
        name=item.get("name") if item.get("name") is not None else None,
        score=float(item.get("score", 0)),
        token_count=int(item.get("token_count", 0)),
    )


def _greedy_select_items_native(
    survivors: list[SearchItemRow],
    entries: list[SkillEntryRef],
    *,
    item_kind: str,
    max_tokens: int,
) -> tuple[list[SearchItemRow], list[MatchedSkill], dict[tuple[str, str], int]] | None:
    from cyt.indexer.bm25_search import greedy_select_skill_items

    payload: list[dict[str, str | float | bool | None]] = []
    lookup = build_entry_lookup(entries)
    for row in survivors:
        entry = entry_for_row(row, entries, lookup=lookup)
        if entry is None:
            continue
        payload.append(_survivor_payload(row, entry, item_kind=item_kind))
    if not payload:
        return None

    try:
        result = greedy_select_skill_items(
            payload,
            item_kind=item_kind,
            max_tokens=max_tokens,
        )
    except Exception:
        logger.exception("greedy_select_skill_items native call failed")
        if not _native_fallback_enabled():
            raise
        return None

    selected_keys = {
        (str(item.get("file_path", "")), str(item.get("item_id", "")))
        for item in result.get("selected", [])
        if isinstance(item, dict)
    }
    trial_tokens: dict[tuple[str, str], int] = {}
    for item in result.get("budget_trace", []):
        if not isinstance(item, dict):
            continue
        key = (str(item.get("file_path", "")), str(item.get("item_id", "")))
        trial_tokens[key] = int(item.get("tokens", 0))

    selected = [row for row in survivors if _item_key(row) in selected_keys]
    matches = [
        _match_from_native(item) for item in result.get("matches", []) if isinstance(item, dict)
    ]
    return selected, matches, trial_tokens


def _greedy_select_items(
    survivors: list[SearchItemRow],
    entries: list[SkillEntryRef],
    *,
    item_kind: str,
    max_tokens: int,
) -> tuple[list[SearchItemRow], list[MatchedSkill], dict[tuple[str, str], int]]:
    ordered = _dedupe_survivors(survivors)
    selected: list[SearchItemRow] = []
    best_matches: list[MatchedSkill] = []
    trial_tokens: dict[tuple[str, str], int] = {}

    index_cache = EntryIndexCache()
    frontmatter_by_doc = _frontmatter_by_doc(entries)
    lookup = build_entry_lookup(entries)
    rows_by_doc: dict[tuple[str, str], list[SearchItemRow]] = {}
    match_by_doc: dict[tuple[str, str], MatchedSkill | None] = {}

    for row in ordered:
        entry = entry_for_row(row, entries, lookup=lookup)
        key = _item_key(row)
        if entry is None:
            trial_tokens[key] = _wrapped_injection_tokens(best_matches) if best_matches else 0
            continue

        doc_key = (entry.entry_dir, entry.doc_id)
        trial_doc_rows = [*rows_by_doc.get(doc_key, []), row]
        top_score = max(item.score for item in trial_doc_rows)
        file_path = trial_doc_rows[0].file_path
        id_specs = sorted(
            {item.item_id for item in trial_doc_rows},
            key=lambda item_id: int(item_id),
        )
        trial_doc_match = reconstruct_doc_match(
            entry,
            id_specs=id_specs,
            item_kind=item_kind,
            file_path=file_path,
            top_score=top_score,
            frontmatter=frontmatter_by_doc.get(doc_key),
            index_cache=index_cache,
        )
        trial_match_by_doc = dict(match_by_doc)
        trial_match_by_doc[doc_key] = trial_doc_match
        trial_matches = [m for m in trial_match_by_doc.values() if m is not None]
        trial_matches.sort(key=lambda item: item.score, reverse=True)
        tokens = _wrapped_injection_tokens(trial_matches)
        trial_tokens[key] = tokens
        if tokens <= max_tokens:
            selected = [*selected, row]
            rows_by_doc[doc_key] = trial_doc_rows
            match_by_doc = trial_match_by_doc
            best_matches = trial_matches

    return selected, best_matches, trial_tokens


def _greedy_select_skills(
    candidates: list[MatchedSkill],
    max_tokens: int,
) -> list[MatchedSkill]:
    from cyt.skills.inject import format_agent_skills, injection_token_count

    ordered = sorted(candidates, key=lambda item: item.score, reverse=True)
    selected: list[MatchedSkill] = []
    for candidate in ordered:
        trial = [*selected, candidate]
        injected = format_agent_skills(trial)
        if not injected:
            continue
        if injection_token_count(injected) <= max_tokens:
            selected = trial
    return selected


def budget_rows_for_item_survivors(
    *,
    survivors: list[SearchItemRow],
    selected: list[SearchItemRow],
    trial_tokens: dict[tuple[str, str], int],
    item_kind: str,
) -> list[BudgetItemRow]:
    selected_keys = {_item_key(row) for row in selected}
    rows: list[BudgetItemRow] = []
    for row in survivors:
        key = _item_key(row)
        rows.append(
            BudgetItemRow(
                file_path=row.file_path,
                item_id=row.item_id,
                item_kind=item_kind,
                score=row.score,
                tokens=trial_tokens.get(key, 0),
                passed=key in selected_keys,
            ),
        )
    return rows


def budget_rows_for_search_survivors(
    *,
    candidates: list[MatchedSkill],
    selected: list[MatchedSkill],
    search_rows: list[SearchItemRow],
) -> list[BudgetItemRow]:
    """Map search survivors to per-item budget pass/below using skill wrapped token counts."""
    if not search_rows:
        return []

    from cyt.skills.inject import format_agent_skills, injection_token_count

    tokens_by_path: dict[str, int] = {}
    for candidate in candidates:
        if candidate.file_path in tokens_by_path:
            continue
        injected = format_agent_skills([candidate])
        tokens_by_path[candidate.file_path] = (
            injection_token_count(injected) if injected else candidate.token_count
        )
    selected_paths = {match.file_path for match in selected}
    rows: list[BudgetItemRow] = []
    for search_row in search_rows:
        if not search_row.passed:
            continue
        rows.append(
            BudgetItemRow(
                file_path=search_row.file_path,
                item_id=search_row.item_id,
                item_kind=search_row.item_kind,
                score=search_row.score,
                tokens=tokens_by_path.get(search_row.file_path, 0),
                passed=search_row.file_path in selected_paths,
            ),
        )
    return rows


def select_items_with_budget_trace(
    *,
    search_rows: list[SearchItemRow],
    entries: list[SkillEntryRef],
    item_kind: str,
    max_tokens: int,
) -> tuple[list[MatchedSkill], list[BudgetItemRow]]:
    """Pool nodes/chunks across skills, greedy select by score, recompose per skill."""
    survivors = [row for row in search_rows if row.passed]
    if max_tokens <= 0 or not survivors:
        return [], budget_rows_for_item_survivors(
            survivors=survivors,
            selected=[],
            trial_tokens={},
            item_kind=item_kind,
        )

    native_result = _greedy_select_items_native(
        survivors,
        entries,
        item_kind=item_kind,
        max_tokens=max_tokens,
    )
    if native_result is not None:
        selected, matches, trial_tokens = native_result
    elif _native_fallback_enabled():
        selected, matches, trial_tokens = _greedy_select_items(
            survivors,
            entries,
            item_kind=item_kind,
            max_tokens=max_tokens,
        )
    else:
        selected, matches, trial_tokens = [], [], {}
    return matches, budget_rows_for_item_survivors(
        survivors=survivors,
        selected=selected,
        trial_tokens=trial_tokens,
        item_kind=item_kind,
    )


def select_skills_with_budget_trace(
    candidates: list[MatchedSkill],
    max_tokens: int,
    *,
    search_rows: list[SearchItemRow],
    entries: list[SkillEntryRef] | None = None,
    item_kind: str = "",
) -> tuple[list[MatchedSkill], list[BudgetItemRow]]:
    """Greedy budget selection plus per-node/chunk budget diagnostics."""
    if entries and item_kind and search_rows:
        return select_items_with_budget_trace(
            search_rows=search_rows,
            entries=entries,
            item_kind=item_kind,
            max_tokens=max_tokens,
        )

    if max_tokens <= 0 or not candidates:
        return [], budget_rows_for_search_survivors(
            candidates=candidates,
            selected=[],
            search_rows=search_rows,
        )

    selected = _greedy_select_skills(candidates, max_tokens)
    return selected, budget_rows_for_search_survivors(
        candidates=candidates,
        selected=selected,
        search_rows=search_rows,
    )


def select_skills_within_budget(
    candidates: list[MatchedSkill],
    max_tokens: int,
) -> list[MatchedSkill]:
    """Greedy add by score with full recompose token measurement."""
    if max_tokens <= 0 or not candidates:
        return []

    return _greedy_select_skills(candidates, max_tokens)


__all__ = [
    "reconstruct_matches_from_items",
    "select_items_with_budget_trace",
    "select_skills_with_budget_trace",
    "select_skills_within_budget",
]

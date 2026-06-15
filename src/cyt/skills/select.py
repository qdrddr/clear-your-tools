"""Greedy skills selection within an injection token budget."""

from __future__ import annotations

from cyt.skills.catalog import SkillEntryRef, _shorten_home_path
from cyt.skills.diagnostics import BudgetItemRow, SearchItemRow
from cyt.skills.search import MatchedSkill


def _item_key(row: SearchItemRow) -> tuple[str, str]:
    return (row.file_path, row.item_id)


def _entry_for_row(row: SearchItemRow, entries: list[SkillEntryRef]) -> SkillEntryRef | None:
    for entry in entries:
        if entry.doc_id == row.doc_id and _shorten_home_path(entry.source_path) == row.file_path:
            return entry
    for entry in entries:
        if entry.doc_id == row.doc_id:
            return entry
    return None


def _reconstruct_matches_from_items(
    items: list[SearchItemRow],
    entries: list[SkillEntryRef],
    *,
    item_kind: str,
) -> list[MatchedSkill]:
    from cyt_indexer import load_skills_index_from_dir, reconstruct_skill_markdown

    from cyt.indexer.tokens import count_tokens
    from cyt.skills.frontmatter import skill_name_from_frontmatter
    from cyt.skills.search import _frontmatter_by_doc

    by_doc: dict[tuple[str, str], list[SearchItemRow]] = {}
    for row in items:
        entry = _entry_for_row(row, entries)
        if entry is None:
            continue
        key = (entry.entry_dir, entry.doc_id)
        by_doc.setdefault(key, []).append(row)

    if not by_doc:
        return []

    frontmatter_by_doc = _frontmatter_by_doc(entries)
    matched: list[MatchedSkill] = []
    for (entry_dir, doc_id), rows in by_doc.items():
        top_score = max(row.score for row in rows)
        file_path = rows[0].file_path
        frontmatter = frontmatter_by_doc.get((entry_dir, doc_id))
        name = skill_name_from_frontmatter(frontmatter)
        id_specs = sorted({row.item_id for row in rows}, key=lambda item_id: int(item_id))
        index = load_skills_index_from_dir(entry_dir)
        if item_kind == "chunk":
            result = reconstruct_skill_markdown(
                index,
                doc_id,
                chunk_id_specs=id_specs,
            )
        else:
            result = reconstruct_skill_markdown(
                index,
                doc_id,
                node_id_specs=id_specs,
            )
        markdown = str(result.get("markdown", "")).strip()
        if not markdown:
            continue
        matched.append(
            MatchedSkill(
                doc_id=doc_id,
                file_path=file_path,
                markdown=markdown,
                name=name,
                score=top_score,
                token_count=count_tokens(markdown),
            ),
        )

    matched.sort(key=lambda item: item.score, reverse=True)
    return matched


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

    for row in ordered:
        trial = [*selected, row]
        matches = _reconstruct_matches_from_items(trial, entries, item_kind=item_kind)
        tokens = _wrapped_injection_tokens(matches)
        key = _item_key(row)
        trial_tokens[key] = tokens
        if tokens <= max_tokens:
            selected = trial
            best_matches = matches

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

    selected, matches, trial_tokens = _greedy_select_items(
        survivors,
        entries,
        item_kind=item_kind,
        max_tokens=max_tokens,
    )
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

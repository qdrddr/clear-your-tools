"""Tests for greedy skills selection."""

from __future__ import annotations

import os
from unittest.mock import patch

from cyt.skills.catalog import SkillEntryRef
from cyt.skills.diagnostics import SearchItemRow
from cyt.skills.reconstruct import EntryIndexCache
from cyt.skills.search import MatchedSkill
from cyt.skills.select import (
    select_items_with_budget_trace,
    select_skills_with_budget_trace,
    select_skills_within_budget,
)


def _skill(doc_id: str, tokens: int, score: float) -> MatchedSkill:
    body = "x" * max(tokens * 4, 8)
    return MatchedSkill(
        doc_id=doc_id,
        file_path=f"/tmp/{doc_id}.md",
        markdown=body,
        name=doc_id,
        score=score,
        token_count=tokens,
    )


def _search_row(
    doc_id: str,
    *,
    item_id: str = "1",
    passed: bool = True,
    score: float = 0.5,
    item_kind: str = "node",
) -> SearchItemRow:
    return SearchItemRow(
        file_path=f"/tmp/{doc_id}.md",
        doc_id=doc_id,
        item_id=item_id,
        item_kind=item_kind,
        score=score,
        passed=passed,
    )


def _entry(doc_id: str) -> SkillEntryRef:
    entry_dir = "/tmp/catalog/entry"
    return SkillEntryRef(
        source_path=f"/tmp/{doc_id}.md",
        doc_id=doc_id,
        content_sha256="abc",
        cache_key="abc",
        entry_dir=entry_dir,
        nodes_dir=f"{entry_dir}/nodes",
        chunk_dir=f"{entry_dir}/chunks/bm25/hash",
        bm25_chunk_dir=f"{entry_dir}/chunks/bm25/hash",
        pipeline="bm25",
        index_params_hash="hash",
        disk_backed=True,
        document={"structure": []},
    )


def test_select_respects_max_tokens() -> None:
    candidates = [
        _skill("a", 100, 0.9),
        _skill("b", 100, 0.8),
        _skill("c", 100, 0.7),
    ]
    selected = select_skills_within_budget(candidates, max_tokens=150)
    assert len(selected) == 1
    assert selected[0].doc_id == "a"


def test_budget_trace_marks_dropped_search_survivors() -> None:
    candidates = [
        _skill("a", 100, 0.9),
        _skill("b", 100, 0.8),
    ]
    search_rows = [
        _search_row("a", item_id="1"),
        _search_row("a", item_id="2"),
        _search_row("b", item_id="3"),
    ]
    selected, budget_rows = select_skills_with_budget_trace(
        candidates,
        max_tokens=150,
        search_rows=search_rows,
    )
    assert [match.doc_id for match in selected] == ["a"]
    assert len(budget_rows) == 3
    passed = {row.item_id for row in budget_rows if row.passed}
    below = {row.item_id for row in budget_rows if not row.passed}
    assert passed == {"1", "2"}
    assert below == {"3"}
    assert all(row.tokens > 0 for row in budget_rows)
    assert budget_rows[0].score == 0.5


def test_budget_trace_ignores_search_rows_that_failed_threshold() -> None:
    candidates = [_skill("a", 100, 0.9)]
    search_rows = [
        _search_row("a", item_id="1", passed=True),
        _search_row("a", item_id="2", passed=False),
    ]
    selected, budget_rows = select_skills_with_budget_trace(
        candidates,
        max_tokens=150,
        search_rows=search_rows,
    )
    assert selected
    assert [row.item_id for row in budget_rows] == ["1"]


def test_item_budget_pools_nodes_across_skills_by_score() -> None:
    search_rows = [
        _search_row("a", item_id="1", score=0.9),
        _search_row("b", item_id="2", score=0.8),
        _search_row("a", item_id="3", score=0.7),
    ]
    entries = [_entry("a"), _entry("b")]

    def fake_reconstruct_doc_match(
        entry: SkillEntryRef,
        *,
        id_specs: list[str],
        item_kind: str,
        file_path: str,
        top_score: float,
        frontmatter: str | None,
        index_cache: EntryIndexCache | None = None,
    ) -> MatchedSkill:
        del item_kind, frontmatter, index_cache
        return MatchedSkill(
            doc_id=entry.doc_id,
            file_path=file_path,
            markdown="body",
            name=entry.doc_id,
            score=top_score,
            token_count=len(id_specs),
        )

    def fake_wrapped_tokens(matches: list[MatchedSkill]) -> int:
        return sum(match.token_count for match in matches) * 50

    with (
        patch.dict(os.environ, {"CYT_DEBUG_NATIVE_FALLBACK": "1"}),
        patch(
            "cyt.skills.select._greedy_select_items_native",
            return_value=None,
        ),
        patch(
            "cyt.skills.select.reconstruct_doc_match",
            side_effect=fake_reconstruct_doc_match,
        ),
        patch(
            "cyt.skills.select._wrapped_injection_tokens",
            side_effect=fake_wrapped_tokens,
        ),
    ):
        selected, budget_rows = select_items_with_budget_trace(
            search_rows=search_rows,
            entries=entries,
            item_kind="node",
            max_tokens=120,
        )

    assert [match.doc_id for match in selected] == ["a", "b"]
    passed = {row.item_id for row in budget_rows if row.passed}
    assert passed == {"1", "2"}
    below = {row.item_id for row in budget_rows if not row.passed}
    assert below == {"3"}
    tokens_by_id = {row.item_id: row.tokens for row in budget_rows}
    assert tokens_by_id["1"] == 50
    assert tokens_by_id["2"] == 100
    assert tokens_by_id["3"] == 150


def test_item_budget_stops_when_next_node_would_exceed_budget() -> None:
    search_rows = [
        _search_row("a", item_id="1", score=0.9),
        _search_row("b", item_id="2", score=0.8),
    ]
    entries = [_entry("a"), _entry("b")]

    def fake_reconstruct_doc_match(
        entry: SkillEntryRef,
        *,
        id_specs: list[str],
        item_kind: str,
        file_path: str,
        top_score: float,
        frontmatter: str | None,
        index_cache: EntryIndexCache | None = None,
    ) -> MatchedSkill:
        del id_specs, item_kind, frontmatter, index_cache
        return MatchedSkill(
            doc_id=entry.doc_id,
            file_path=file_path,
            markdown="body",
            name=entry.doc_id,
            score=top_score,
            token_count=1,
        )

    def fake_wrapped_tokens(matches: list[MatchedSkill]) -> int:
        return len(matches) * 100

    with (
        patch.dict(os.environ, {"CYT_DEBUG_NATIVE_FALLBACK": "1"}),
        patch(
            "cyt.skills.select._greedy_select_items_native",
            return_value=None,
        ),
        patch(
            "cyt.skills.select.reconstruct_doc_match",
            side_effect=fake_reconstruct_doc_match,
        ),
        patch(
            "cyt.skills.select._wrapped_injection_tokens",
            side_effect=fake_wrapped_tokens,
        ),
    ):
        selected, budget_rows = select_items_with_budget_trace(
            search_rows=search_rows,
            entries=entries,
            item_kind="node",
            max_tokens=120,
        )

    assert [match.doc_id for match in selected] == ["a"]
    passed = {row.item_id for row in budget_rows if row.passed}
    assert passed == {"1"}
    below = {row.item_id for row in budget_rows if not row.passed}
    assert below == {"2"}
    tokens_by_id = {row.item_id: row.tokens for row in budget_rows}
    assert tokens_by_id["1"] == 100
    assert tokens_by_id["2"] == 200

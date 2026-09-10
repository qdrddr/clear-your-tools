"""Unit tests for tool example query ranking."""

from __future__ import annotations

from cyt.tool_examples.query import rank_by_query


def test_rank_by_query_prefers_matching_terms() -> None:
    items = [
        ("a", "bm25 ranking search graph", 100),
        ("b", "unrelated settings config", 200),
        ("c", "ranking algorithm bm25", 50),
    ]
    ranked = rank_by_query("bm25 ranking", items)
    top_two = {str(item.item) for item in ranked[:2]}
    assert top_two == {"a", "c"}
    assert str(ranked[-1].item) == "b"


def test_rank_by_query_falls_back_to_recency_without_query_tokens() -> None:
    items = [
        ("old", "alpha", 10),
        ("mid", "beta", 20),
        ("new", "gamma", 30),
    ]
    ranked = rank_by_query("", items)
    assert [str(item.item) for item in ranked] == ["new", "mid", "old"]


def test_rank_by_query_recency_breaks_ties() -> None:
    items = [
        ("older", "project demo query", 100),
        ("newer", "project demo query", 200),
    ]
    ranked = rank_by_query("project demo", items)
    assert str(ranked[0].item) == "newer"

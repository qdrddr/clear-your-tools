"""BM25 similarity normalization invariants (mutation killers)."""

from __future__ import annotations

from itertools import pairwise

from cyt.pruners.bm25 import normalize_bm25_similarity, normalize_bm25_similarity_array


def test_normalize_bm25_similarity_clamps_non_positive_to_zero() -> None:
    assert normalize_bm25_similarity(0.0) == 0.0
    assert normalize_bm25_similarity(-0.001) == 0.0
    assert normalize_bm25_similarity(-100.0) == 0.0


def test_normalize_bm25_similarity_is_monotonic_and_bounded() -> None:
    raw_scores = [0.0, 0.25, 0.5, 1.0, 2.0, 5.0, 20.0]
    normalized = [normalize_bm25_similarity(score) for score in raw_scores]
    assert all(0.0 <= value <= 1.0 for value in normalized)
    for left, right in pairwise(normalized):
        assert left <= right


def test_normalize_bm25_similarity_array_matches_scalar_map() -> None:
    raw_scores = [0.0, 1.2196, 1.4156, 100.0]
    assert normalize_bm25_similarity_array(raw_scores) == [
        normalize_bm25_similarity(score) for score in raw_scores
    ]


def test_normalize_bm25_similarity_higher_raw_yields_higher_similarity() -> None:
    low = normalize_bm25_similarity(1.2196)
    high = normalize_bm25_similarity(1.4156)
    assert high > low
    assert high < 1.0

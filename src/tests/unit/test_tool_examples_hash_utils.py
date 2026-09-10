"""Unit tests for canonical JSON hashing."""

from __future__ import annotations

from cyt.tool_examples.hash_utils import canonical_json, content_hash


def test_canonical_json_sorts_keys() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_content_hash_stable_for_equivalent_objects() -> None:
    left = {"query": "bm25", "limit": 10}
    right = {"limit": 10, "query": "bm25"}
    assert content_hash(left) == content_hash(right)


def test_content_hash_differs_for_different_objects() -> None:
    assert content_hash({"query": "a"}) != content_hash({"query": "b"})

"""Tests for injection schema/example alignment."""

from __future__ import annotations

from cyt.tools.injection_schema import (
    entangle_examples_with_schema,
    ensure_required_properties_in_schema,
    project_example_to_schema,
)


def test_project_example_drops_unknown_and_invalid_keys() -> None:
    schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "depth": {"type": "integer"},
        },
        "required": ["question"],
    }
    assert project_example_to_schema({"limit": 10, "query": "BM25"}, schema) is None
    assert project_example_to_schema(
        {"question": "BM25", "depth": 2},
        schema,
    ) == {"question": "BM25", "depth": 2}


def test_entangle_examples_filters_list() -> None:
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    examples = [
        {"query": "bm25"},
        {"limit": 5},
        {"pattern": "bm25"},
    ]
    assert entangle_examples_with_schema(examples, schema) == [{"query": "bm25"}]


def test_ensure_required_properties_in_schema_merges_missing_required() -> None:
    pruned = {
        "type": "object",
        "properties": {"depth": {"type": "integer"}},
    }
    full = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "depth": {"type": "integer"},
        },
        "required": ["question"],
    }
    merged = ensure_required_properties_in_schema(pruned, full)
    assert "question" in merged["properties"]
    assert merged["required"] == ["question"]

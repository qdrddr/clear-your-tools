"""Tests for injection schema/example alignment."""

from __future__ import annotations

from cyt.tools.injection_schema import (
    ensure_required_properties_in_schema,
    entangle_examples_with_schema,
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
    examples: list[object] = [
        {"query": "bm25"},
        {"limit": 5},
        {"pattern": "bm25"},
    ]
    assert entangle_examples_with_schema(examples, schema) == [{"query": "bm25"}]


def test_entangle_examples_dedupes_after_schema_projection() -> None:
    schema = {
        "type": "object",
        "properties": {"project": {"type": "string"}},
        "required": ["project"],
    }
    examples = [
        {"project": "clear-your-tools", "query": "BM25 scoring ranking search"},
        {
            "project": "clear-your-tools",
            "query": "BM25 scoring search ranking implementation",
            "limit": 20,
        },
        {
            "project": "clear-your-tools",
            "name_pattern": ".*(score_corpus|build_ram_index).*",
            "limit": 15,
        },
    ]
    assert entangle_examples_with_schema(examples, schema) == [{"project": "clear-your-tools"}]


def test_entangle_examples_keeps_distinct_projected_shapes() -> None:
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "repo": {"type": "string"},
        },
        "required": ["query", "repo"],
    }
    examples = [
        {"query": "BM25 scoring", "repo": "/path/a"},
        {"query": "BM25 ranking", "repo": "/path/b"},
        {"query": "BM25 scoring", "repo": "/path/a"},
    ]
    assert entangle_examples_with_schema(examples, schema) == [
        {"query": "BM25 scoring", "repo": "/path/a"},
        {"query": "BM25 ranking", "repo": "/path/b"},
    ]


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

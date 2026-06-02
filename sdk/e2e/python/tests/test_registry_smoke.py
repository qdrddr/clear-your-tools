"""Smoke tests for cyt-indexer-sdk installed from PyPI."""

from __future__ import annotations

from cyt_indexer import build_catalog_index, count_tokens


def test_count_tokens_from_registry() -> None:
    assert count_tokens("hello") > 0


def test_build_catalog_index_from_registry() -> None:
    tool = {
        "id": "mcp__test__foo",
        "server": "test",
        "tool": "mcp__test__foo",
        "summary": "A test tool",
        "full_schema": {
            "id": "mcp__test__foo",
            "name": "mcp__test__foo",
            "description": "A test tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "required_field": {"type": "string"},
                    "optional_field": {"type": "string", "description": "opt"},
                },
                "required": ["required_field"],
            },
        },
    }
    index = build_catalog_index([tool], [])
    assert "schemas/decomposed/mcp__test__foo.json" in index.files

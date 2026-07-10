"""Tests for cyt_indexer.removed_chunks (Python SDK parity — direct SDK import allowed)."""

from __future__ import annotations

from cyt_indexer import chunk_survivor_key, removed_chunks


def test_removed_chunks_excludes_survivors_by_decomposed_key() -> None:
    full = {
        "json": [
            {
                "file_path": "schemas/decomposed/Agent.json",
                "content": {"name": "Agent"},
            },
            {
                "file_path": "schemas/decomposed/Agent/extra.json",
                "content": {},
            },
        ],
        "md": [
            {"file_path": "schemas/decomposed/haiku.md", "content": "haiku"},
            {"file_path": "schemas/decomposed/sonnet.md", "content": "sonnet"},
        ],
    }
    surviving = {
        "json": [{"file_path": "src/catalog/schemas/decomposed/Agent.json"}],
        "md": [{"file_path": "src/catalog/schemas/decomposed/haiku.md"}],
    }
    removed = removed_chunks(full, surviving)
    assert len(removed["json"]) == 1
    assert removed["json"][0]["file_path"] == "schemas/decomposed/Agent/extra.json"
    assert len(removed["md"]) == 1
    assert removed["md"][0]["file_path"] == "schemas/decomposed/sonnet.md"


def test_chunk_survivor_key_normalizes_paths() -> None:
    assert (
        chunk_survivor_key(
            {"file_path": "src/catalog/schemas/decomposed/Agent.json"},
            "json",
        )
        == "schemas/decomposed/Agent.json"
    )

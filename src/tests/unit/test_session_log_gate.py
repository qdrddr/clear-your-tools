"""Tests for session-log injection mode resolution."""

from __future__ import annotations

from typing import Any

from cyt.injection.session_log import SessionLogIndex, combined_session_text, resolve_injection_mode
from cyt_client.sessions import entries_after_latest_compaction


def test_satisfied_full_suppresses_reinjection() -> None:
    index = SessionLogIndex(
        entries=(
            {
                "kind": "tool",
                "key": "tool:Shell",
                "hash": "hash-v2",
                "full": True,
                "name": "Shell",
            },
        ),
    )
    mode = resolve_injection_mode(
        key="tool:Shell",
        current_hash="hash-v2",
        index=index,
        session_text="",
        formatted_skinny="<tool name='Shell'></tool>",
        formatted_full="<tool name='Shell'>full</tool>",
    )
    assert mode == "skip"


def test_hash_mismatch_promotes_to_full() -> None:
    index = SessionLogIndex(
        entries=(
            {
                "kind": "tool",
                "key": "tool:Shell",
                "hash": "hash-v1",
                "full": False,
                "name": "Shell",
            },
        ),
    )
    mode = resolve_injection_mode(
        key="tool:Shell",
        current_hash="hash-v2",
        index=index,
        session_text="",
        formatted_skinny="<tool name='Shell'>skinny</tool>",
        formatted_full="<tool name='Shell'>full</tool>",
    )
    assert mode == "full"


def test_three_strikes_promotes_to_full() -> None:
    entries = tuple(
        {"kind": "skill", "key": "skill:demo", "hash": "h1", "full": False} for _ in range(3)
    )
    index = SessionLogIndex(entries=entries)
    mode = resolve_injection_mode(
        key="skill:demo",
        current_hash="h1",
        index=index,
        session_text="",
        formatted_skinny="<skill>skinny</skill>",
        formatted_full="<skill>full</skill>",
    )
    assert mode == "full"


def test_legacy_key_alias_suppresses_reinjection() -> None:
    index = SessionLogIndex(
        entries=(
            {
                "kind": "tool",
                "key": "tool:Shell",
                "hash": "hash-v2",
                "full": True,
                "name": "Shell",
            },
        ),
    )
    mode = resolve_injection_mode(
        key="tool:executor:Shell",
        current_hash="hash-v2",
        index=index,
        session_text="",
        formatted_skinny="<tool name='Shell'></tool>",
        formatted_full="<tool name='Shell'>full</tool>",
        key_aliases=("tool:Shell",),
    )
    assert mode == "skip"


def test_combined_session_text_includes_turn_corpus() -> None:
    index = SessionLogIndex(
        entries=(
            {
                "kind": "turn",
                "key": "turn:1",
                "prompt": "analyze graph",
                "assistant": "checking",
            },
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:codebase-memory-mcp_search_graph",
                "name": "codebase-memory-mcp_search_graph",
                "catalog": "cyt_mcp",
                "full": True,
                "input_schema": {"type": "object", "properties": {"project": {"type": "string"}}},
                "description": "graph search",
            },
        ),
    )
    combined = combined_session_text("", index)
    assert "analyze graph" in combined
    assert "codebase-memory-mcp_search_graph" in combined


def test_post_compaction_index_allows_reinject() -> None:
    entries: list[dict[str, Any]] = [
        {
            "kind": "tool",
            "key": "tool:demo",
            "hash": "hash-v1",
            "full": True,
            "name": "demo",
        },
        {"kind": "compaction", "key": "compaction", "payload": {}},
    ]
    sliced = entries_after_latest_compaction(entries)
    index = SessionLogIndex(entries=tuple(sliced))
    mode = resolve_injection_mode(
        key="tool:demo",
        current_hash="hash-v1",
        index=index,
        session_text="",
        formatted_skinny="<tool name='demo'></tool>",
        formatted_full="<tool name='demo'>full</tool>",
    )
    assert mode == "skinny"

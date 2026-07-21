"""Tests for session-log injection mode resolution."""

from __future__ import annotations

from cyt.injection.session_log import SessionLogIndex, resolve_injection_mode


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

"""Tests for agent hook payload normalization."""

from __future__ import annotations

from cyt.skills.hook_payload import (
    hook_event_name,
    model_from_payload,
    normalize_hook_payload,
    prompt_from_payload,
    session_id,
)


def test_normalize_flat_claude_session_start() -> None:
    raw = {
        "session_id": "11b09b4b-f335-4a08-b618-8f607f6d7a46",
        "hook_event_name": "SessionStart",
        "model": "google/gemini-3-flash-preview",
        "source": "startup",
    }
    normalized = normalize_hook_payload(raw)
    assert session_id(normalized) == "11b09b4b-f335-4a08-b618-8f607f6d7a46"
    assert model_from_payload(normalized) == "google/gemini-3-flash-preview"
    assert hook_event_name(normalized) == "SessionStart"


def test_normalize_nested_payload_fields() -> None:
    raw = {
        "hook_event_name": "UserPromptSubmit",
        "payload": {
            "session_id": "sess-nested",
            "prompt": "hello nested",
        },
    }
    normalized = normalize_hook_payload(raw)
    assert hook_event_name(normalized) == "UserPromptSubmit"
    assert session_id(normalized) == "sess-nested"
    assert prompt_from_payload(normalized) == "hello nested"


def test_normalize_top_level_wins_over_nested_payload() -> None:
    raw = {
        "hook_event_name": "SessionStart",
        "session_id": "top-level",
        "payload": {
            "session_id": "nested",
            "model": "nested-model",
        },
    }
    normalized = normalize_hook_payload(raw)
    assert session_id(normalized) == "top-level"
    assert model_from_payload(normalized) == "nested-model"

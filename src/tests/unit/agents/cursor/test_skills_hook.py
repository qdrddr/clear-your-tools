"""Tests for Cursor skills_hook normalize and transcript parsing."""

from __future__ import annotations

import json

from cyt.agents.cursor.skills_hook import (
    last_assistant_from_records,
    looks_like_cursor_hook,
    normalize_cursor_payload,
)
from cyt.skills.hook_payload import normalize_hook_payload
from cyt.skills.transcript import last_assistant_from_payload
from tests.support.paths import FIXTURES_DIR


def test_normalize_cursor_payload_maps_events_and_workspace() -> None:
    payload = {
        "hookEventName": "beforeSubmitPrompt",
        "workspace_roots": ["/tmp/project"],
        "conversation_id": "conv-123",
        "prompt": "hello",
    }
    normalized = normalize_cursor_payload(payload)
    assert normalized["hook_event_name"] == "UserPromptSubmit"
    assert normalized["cwd"] == "/tmp/project"
    assert normalized["session_id"] == "conv-123"


def test_normalize_hook_payload_dispatches_cursor_by_cyt_agent() -> None:
    payload = {
        "cyt_agent": "cursor",
        "hookEventName": "beforeSubmitPrompt",
        "workspace_roots": ["/tmp/ws"],
        "prompt": "hi",
    }
    normalized = normalize_hook_payload(payload)
    assert normalized["hook_event_name"] == "UserPromptSubmit"
    assert normalized["cwd"] == "/tmp/ws"


def test_cursor_transcript_last_assistant() -> None:
    fixture = FIXTURES_DIR / "transcripts" / "cursor_agent.jsonl"
    records = [
        json.loads(line)
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert (
        last_assistant_from_records(records)
        == "Here is the final answer with implementation details."
    )


def test_last_assistant_from_payload_with_inline_cursor_transcript() -> None:
    fixture = FIXTURES_DIR / "transcripts" / "cursor_agent.jsonl"
    records = [
        json.loads(line)
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    payload = {
        "cyt_agent": "cursor",
        "prompt": "Implement the plan.",
        "cyt_transcript": records,
    }
    assistant = last_assistant_from_payload(payload, allow_file_read=False)
    assert assistant == "Here is the final answer with implementation details."


def test_looks_like_cursor_hook() -> None:
    assert looks_like_cursor_hook({"hookEventName": "beforeSubmitPrompt"})
    assert looks_like_cursor_hook({"workspace_roots": ["/x"]})
    assert not looks_like_cursor_hook({"hook_event_name": "UserPromptSubmit"})

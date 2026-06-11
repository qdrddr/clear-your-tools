"""Tests for hook transcript assistant extraction."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cyt.skills.transcript import (
    last_assistant_from_transcript,
    model_from_transcript,
    skills_search_query_from_hook_payload,
)


def test_last_assistant_from_claude_transcript() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claude.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "user",
                            "message": {"role": "user", "content": "hello"},
                        },
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {"type": "thinking", "thinking": "internal"},
                                    {"type": "text", "text": "CYT is a reverse proxy."},
                                ],
                            },
                        },
                    ),
                ],
            ),
            encoding="utf-8",
        )
        assert last_assistant_from_transcript(str(path)) == "CYT is a reverse proxy."


def test_last_assistant_from_claude_code_message_only_record() -> None:
    """Claude Code jsonl often omits top-level type: assistant."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claude-code.jsonl"
        path.write_text(
            json.dumps(
                {
                    "parentUuid": "e96974c0-9673-41a1-a558-84bdc8d6a85b",
                    "isSidechain": False,
                    "message": {
                        "id": "gen-1781184278-fSggtaZssmOgs6RDPkyE",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Updated the LLM skills pruner."}],
                    },
                },
            ),
            encoding="utf-8",
        )
        assert last_assistant_from_transcript(str(path)) == "Updated the LLM skills pruner."


def test_last_assistant_from_claude_code_type_assistant_at_end() -> None:
    """Claude Code jsonl may place type: assistant after the message object."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claude-code.jsonl"
        path.write_text(
            json.dumps(
                {
                    "parentUuid": "e96974c0-9673-41a1-a558-84bdc8d6a85b",
                    "isSidechain": False,
                    "message": {
                        "id": "gen-1781184278-fSggtaZssmOgs6RDPkyE",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "CYT reverse proxy summary."}],
                    },
                    "type": "assistant",
                    "sessionId": "399056d7-7c06-4748-bf1a-041ddeb6a7cf",
                },
            ),
            encoding="utf-8",
        )
        assert last_assistant_from_transcript(str(path)) == "CYT reverse proxy summary."


def test_last_assistant_from_codex_response_item_record() -> None:
    """Codex jsonl stores assistant turns under response_item.payload."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "codex.jsonl"
        path.write_text(
            json.dumps(
                {
                    "timestamp": "2026-06-10T21:43:24.146Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Clear Your Tools prunes agent tools.",
                            },
                        ],
                        "phase": "final_answer",
                    },
                },
            ),
            encoding="utf-8",
        )
        assert last_assistant_from_transcript(str(path)) == "Clear Your Tools prunes agent tools."


def test_last_assistant_from_codex_transcript_prefers_final_answer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "codex.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "checking docs"}],
                                "phase": "commentary",
                            },
                        },
                    ),
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "Clear Your Tools prunes agent tools.",
                                    },
                                ],
                                "phase": "final_answer",
                            },
                        },
                    ),
                ],
            ),
            encoding="utf-8",
        )
        assert last_assistant_from_transcript(str(path)) == "Clear Your Tools prunes agent tools."


def test_last_assistant_missing_transcript_returns_none() -> None:
    assert last_assistant_from_transcript("/tmp/does-not-exist.jsonl") is None


def test_model_from_claude_transcript() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claude.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "user",
                            "message": {"role": "user", "content": "hello"},
                        },
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "model": "google/gemini-3-flash-preview-20251217",
                                "content": [{"type": "text", "text": "hi"}],
                            },
                        },
                    ),
                ],
            ),
            encoding="utf-8",
        )
        assert model_from_transcript(str(path)) == "google/gemini-3-flash-preview-20251217"


def test_model_from_transcript_prefers_latest_assistant() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claude.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "model": "claude-haiku-4",
                                "content": [{"type": "text", "text": "old"}],
                            },
                        },
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "model": "claude-sonnet-4",
                                "content": [{"type": "text", "text": "new"}],
                            },
                        },
                    ),
                ],
            ),
            encoding="utf-8",
        )
        assert model_from_transcript(str(path)) == "claude-sonnet-4"


def test_model_from_transcript_missing_file_returns_none() -> None:
    assert model_from_transcript("/tmp/does-not-exist.jsonl") is None


def test_model_from_transcript_user_only_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claude.jsonl"
        path.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "first turn"},
                },
            ),
            encoding="utf-8",
        )
        assert model_from_transcript(str(path)) is None


def test_skills_search_query_from_hook_payload_includes_assistant() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "prior answer"}],
                    },
                },
            ),
            encoding="utf-8",
        )
        query = skills_search_query_from_hook_payload(
            {
                "prompt": "next step",
                "transcript_path": str(transcript),
            },
        )
        assert query == "User_Asks: next step; Assistant_Says: prior answer"


def test_skills_search_query_from_hook_payload_user_only_without_transcript() -> None:
    query = skills_search_query_from_hook_payload({"prompt": "only user"})
    assert query == "User_Asks: only user"


def test_skills_search_query_with_transcript_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "codex reply"}],
                        "phase": "final_answer",
                    },
                },
            ),
            encoding="utf-8",
        )
        from cyt.skills.transcript import skills_search_query

        query = skills_search_query(
            "continue",
            transcript_path=str(transcript),
        )
        assert query == "User_Asks: continue; Assistant_Says: codex reply"

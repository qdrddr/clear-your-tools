"""Tests for hook transcript assistant extraction."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from cyt.skills.agents import CYT_LAUNCH_AGENT_ENV
from cyt.skills.transcript import (
    infer_transcript_agent,
    last_assistant_from_payload,
    last_assistant_from_transcript,
    model_from_transcript,
    resolve_model,
    skills_search_query_from_hook_payload,
    transcript_records_from_payload,
    transcript_source_from_payload,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "transcripts"


def _load_fixture(name: str) -> list[object]:
    return cast(list[object], json.loads((_FIXTURES / name).read_text(encoding="utf-8")))


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


def test_infer_transcript_agent_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CYT_LAUNCH_AGENT_ENV, raising=False)
    assert infer_transcript_agent("/Users/you/.codex/sessions/rollout.jsonl") == "codex"
    assert infer_transcript_agent("/Users/you/.claude/projects/foo/session.jsonl") == "claude"
    assert infer_transcript_agent("/tmp/session.jsonl") is None


def test_infer_transcript_agent_falls_back_to_launch_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "codex")
    assert infer_transcript_agent("/tmp/session.jsonl") == "codex"
    assert infer_transcript_agent(None) == "codex"


def test_transcript_records_prefers_cyt_transcript() -> None:
    records = _load_fixture("claude_assistant.json")
    payload = {
        "transcript_path": "/tmp/missing.jsonl",
        "cyt_transcript": records,
    }
    assert transcript_records_from_payload(payload, allow_file_read=True) == records
    assert transcript_source_from_payload(payload, allow_file_read=True) == "inline"


def test_http_mode_does_not_read_transcript_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "from file"}],
                    },
                },
            ),
            encoding="utf-8",
        )
        payload = {
            "prompt": "next",
            "transcript_path": str(transcript),
        }
        with patch.object(Path, "read_text", side_effect=AssertionError("file read not allowed")):
            assert transcript_records_from_payload(payload, allow_file_read=False) is None
            assert last_assistant_from_payload(payload, allow_file_read=False) is None
            assert resolve_model(payload, allow_file_read=False) is None
            query = skills_search_query_from_hook_payload(payload, allow_file_read=False)
            assert query == "User_Asks: next"


def test_http_mode_uses_inline_cyt_transcript_without_file_read() -> None:
    records = _load_fixture("claude_assistant.json")
    payload = {
        "prompt": "next",
        "transcript_path": "/tmp/unused.jsonl",
        "cyt_transcript": records,
    }
    with patch.object(Path, "read_text", side_effect=AssertionError("file read not allowed")):
        assert (
            last_assistant_from_payload(payload, allow_file_read=False) == "Claude assistant reply."
        )
        assert resolve_model(payload, allow_file_read=False) == "claude-sonnet-4"
        query = skills_search_query_from_hook_payload(payload, allow_file_read=False)
        assert query == "User_Asks: next; Assistant_Says: Claude assistant reply."


def test_stdin_mode_reads_transcript_file_when_inline_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "model": "claude-haiku-4",
                        "content": [{"type": "text", "text": "from file"}],
                    },
                },
            ),
            encoding="utf-8",
        )
        payload = {"prompt": "next", "transcript_path": str(transcript)}
        assert last_assistant_from_payload(payload, allow_file_read=True) == "from file"
        assert resolve_model(payload, allow_file_read=True) == "claude-haiku-4"
        assert transcript_source_from_payload(payload, allow_file_read=True) == "file"


def test_resolve_model_codex_turn_context_from_fixture() -> None:
    records = _load_fixture("codex_session.json")
    payload = {
        "transcript_path": "/Users/you/.codex/sessions/rollout.jsonl",
        "cyt_transcript": records,
    }
    assert resolve_model(payload, allow_file_read=False) == "gpt-5.4-mini"
    assert last_assistant_from_payload(payload, allow_file_read=False) == "Codex assistant reply."


def test_resolve_model_payload_beats_transcript() -> None:
    records = _load_fixture("claude_assistant.json")
    payload = {
        "model": "payload-model",
        "cyt_transcript": records,
    }
    assert resolve_model(payload, allow_file_read=False) == "payload-model"

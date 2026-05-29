"""Tests for OpenAI Responses API proxy request transform."""

from __future__ import annotations

from unittest.mock import patch

from cyt.proxy.anthropic import PruneResult
from cyt.proxy.openai_responses import (
    clean_input,
    extract_user_query_from_input,
    transform_openai_request,
)


def _user_message(*texts: str) -> dict:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text} for text in texts],
    }


def _developer_message(text: str) -> dict:
    return {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": text}],
    }


def test_clean_input_drops_system_reminder_blocks() -> None:
    input_items = [
        _user_message("<system-reminder>\nnoise\n</system-reminder>", "real task"),
    ]
    cleaned = clean_input(input_items)
    assert len(cleaned) == 1
    assert cleaned[0]["content"] == "real task"


def test_extract_user_query_from_openai_input_finds_last_user_message() -> None:
    input_items = [
        _developer_message("permissions and skills"),
        _user_message("# AGENTS.md instructions", "<environment_context>"),
        _developer_message("shell hook reminder"),
        _user_message("say hi!"),
    ]
    cleaned = clean_input(input_items)
    assert extract_user_query_from_input(cleaned) == "say hi!"


def test_extract_user_query_from_input_uses_latest_user_turn() -> None:
    input_items = [
        _user_message("update src/retrieve_catalog.py with score filtering"),
        _user_message(
            "The user stepped away and is coming back. "
            "Recap in under 40 words, 1-2 plain sentences.",
        ),
    ]
    cleaned = clean_input(input_items)
    assert (
        extract_user_query_from_input(cleaned)
        == "The user stepped away and is coming back. Recap in under 40 words, 1-2 plain sentences."
    )


def test_transform_openai_request_only_changes_tools() -> None:
    body = {
        "model": "gpt-5.4-mini",
        "instructions": "You are Codex.",
        "input": [
            _developer_message("developer context"),
            _user_message("find tools"),
        ],
        "tools": [
            {
                "type": "function",
                "name": "mcp__srv__tool_a",
                "description": "A",
                "strict": False,
                "parameters": {"type": "object", "properties": {}},
            },
        ],
        "stream": True,
    }
    pruned_tools = [
        {
            "type": "function",
            "name": "mcp__srv__tool_a",
            "description": "A",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    ]
    prune_result = PruneResult(
        tools=pruned_tools,
        status="applied",
        query="find tools",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=1,
        error=None,
    )

    with patch("cyt.proxy.openai_responses.filter_tools_for_query", return_value=prune_result):
        out, meta = transform_openai_request(body)

    assert out["tools"] == pruned_tools
    assert out["input"] == body["input"]
    assert out["instructions"] == "You are Codex."
    assert out["stream"] is True
    assert out["model"] == "gpt-5.4-mini"
    assert meta is not None
    assert meta.status == "applied"


def test_transform_openai_request_passthrough_when_no_prune() -> None:
    body = {
        "model": "gpt-test",
        "input": [_user_message("hi")],
        "tools": [
            {
                "type": "function",
                "name": "mcp__a__b",
                "parameters": {},
            },
        ],
    }
    failed = PruneResult(
        tools=None,
        status="failed",
        query="hi",
        tools_in=1,
        mcp_tools_in=1,
        tools_out=None,
        error="api error",
    )
    with patch("cyt.proxy.openai_responses.filter_tools_for_query", return_value=failed):
        out, meta = transform_openai_request(body)
    assert out == body
    assert meta is not None
    assert meta.status == "failed"

"""Unit tests for cyt_client session capture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt_client.session_capture import (
    extract_cyt_mcp_search_result,
    is_post_tool_capture_event,
    merge_tool_into_cyt_mcp_catalog,
    persist_cyt_mcp_search_result,
    persist_turn_to_session_log,
)


def test_is_post_tool_capture_event() -> None:
    assert is_post_tool_capture_event({"hook_event_name": "postToolUse"})
    assert is_post_tool_capture_event({"hook_event_name": "PostToolUse"})
    assert not is_post_tool_capture_event({"hook_event_name": "preToolUse"})


def test_extract_cursor_post_tool_payload() -> None:
    definition = {
        "name": "codebase-memory-mcp_search_graph",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}},
    }
    payload = {
        "hook_event_name": "postToolUse",
        "tool_name": "MCP:cyt-mcp_get-tool-definitions",
        "tool_input": {"tool_name": "codebase-memory-mcp_search_graph"},
        "tool_output": json.dumps(definition),
    }
    extracted = extract_cyt_mcp_search_result(payload)
    assert extracted == ("codebase-memory-mcp_search_graph", definition)


def test_build_tool_catalog_entry_from_search() -> None:
    definition = {
        "name": "codebase-memory-mcp_search_graph",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}},
        "description": "graph search",
        "outputSchema": {"type": "object"},
    }
    entry = merge_tool_into_cyt_mcp_catalog(
        Path("unused"),
        "codebase-memory-mcp_search_graph",
        definition,
    )
    assert entry["kind"] == "tool_catalog"
    assert entry["catalog"] == "cyt_mcp"
    assert entry["tools"][0]["name"] == "codebase-memory-mcp_search_graph"
    assert entry["tools"][0]["input_schema"] == definition["inputSchema"]


def test_persist_search_result_dedupes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "session.jsonl"
    definition = {
        "name": "codebase-memory-mcp_search_graph",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}},
    }
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "session-1",
        "tool_name": "mcp__cyt-mcp__get-tool-definitions",
        "tool_input": {"tool_name": "codebase-memory-mcp_search_graph"},
        "tool_output": json.dumps(definition),
    }
    monkeypatch.setattr("cyt_client.session_capture.session_log_path", lambda _payload: log_path)
    assert persist_cyt_mcp_search_result(payload) is True
    assert persist_cyt_mcp_search_result(payload) is False
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1


def test_persist_turn_to_session_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "session.jsonl"
    payload = {
        "hook_event_name": "beforeSubmitPrompt",
        "session_id": "session-1",
        "prompt": "hello",
        "cyt_transcript": [
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "hi there"}]},
            },
        ],
    }
    monkeypatch.setattr("cyt_client.session_capture.session_log_path", lambda _payload: log_path)
    assert persist_turn_to_session_log(payload) is True
    entry = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["kind"] == "turn"
    assert entry["prompt"] == "hello"
    assert entry["assistant"] == "hi there"

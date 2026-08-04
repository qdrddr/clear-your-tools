"""Unit tests for cyt-client pre-toolcall gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt_client.tool_gate import (
    is_cyt_mcp_get_tool_definitions_tool,
    normalize_mcp_tool_name,
    validate_pre_tool_call,
)

_GET_TOOL_DEFINITIONS_TOOL = "cyt-mcp_get-tool-definitions"


def test_normalize_codex_mcp_name() -> None:
    assert normalize_mcp_tool_name("mcp__filesystem__read_file", agent="codex") == (
        "filesystem_read_file"
    )


def test_validate_denies_unknown_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:filesystem_read_file",
                "name": "filesystem_read_file",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    allowed, reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "filesystem_write_file",
        },
    )
    assert allowed is False
    assert "not injected" in reason


def test_validate_denies_bad_property(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:filesystem_read_file",
                "name": "filesystem_read_file",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    allowed, reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "filesystem_read_file",
            "tool_input": {"bogus": "x"},
        },
    )
    assert allowed is False
    assert "unknown property" in reason


def test_validate_allows_get_tool_definitions_without_session_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    allowed, reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": _GET_TOOL_DEFINITIONS_TOOL,
            "tool_input": {"tool_name": "codebase-memory-mcp_search_graph"},
        },
    )
    assert allowed is True
    assert reason == ""


def test_validate_allows_get_tool_definitions_resolved_backend_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:codebase-memory-mcp_search_graph",
                "name": "codebase-memory-mcp_search_graph",
                "catalog": "cyt_mcp",
                "source": "cyt-mcp_get-tool-definitions",
                "input_schema": {
                    "type": "object",
                    "properties": {"project": {"type": "string"}},
                },
            },
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    allowed, _reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "codebase-memory-mcp_search_graph",
            "tool_input": {"project": "demo"},
        },
    )
    assert allowed is True


def test_is_cyt_mcp_get_tool_definitions_tool_normalizes_codex_name() -> None:
    assert is_cyt_mcp_get_tool_definitions_tool(
        "mcp__cyt-mcp__get-tool-definitions",
        agent="codex",
    )


def test_is_cyt_mcp_get_tool_definitions_tool_normalizes_cursor_wire_name() -> None:
    assert is_cyt_mcp_get_tool_definitions_tool("get-tool-definitions", agent="cursor")
    assert is_cyt_mcp_get_tool_definitions_tool("MCP:get-tool-definitions", agent="cursor")


def test_validate_denies_get_tool_definitions_without_tool_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: None,
    )
    allowed, reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": _GET_TOOL_DEFINITIONS_TOOL,
        },
    )
    assert allowed is False
    assert "tool_name is required" in reason


def test_validate_denies_get_tool_definitions_with_empty_tool_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: None,
    )
    allowed, reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": _GET_TOOL_DEFINITIONS_TOOL,
            "tool_input": {"tool_name": ""},
        },
    )
    assert allowed is False
    assert "tool_name is required" in reason


def test_non_cyt_mcp_tool_allowed_without_session_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: None,
    )
    allowed, reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "Shell",
            "tool_input": {"command": "echo hi"},
        },
    )
    assert allowed is True
    assert reason == ""


def test_cyt_mcp_backend_denied_without_session_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: None,
    )
    allowed, reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "codebase-memory-mcp_query_graph",
            "tool_input": {"project": "demo", "query": "MATCH (n) RETURN n"},
        },
    )
    assert allowed is False
    assert "not injected" in reason


def test_get_tool_definitions_allowed_without_session_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: None,
    )
    allowed, reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": _GET_TOOL_DEFINITIONS_TOOL,
            "tool_input": {"tool_name": "codebase-memory-mcp_search_graph"},
        },
    )
    assert allowed is True
    assert reason == ""


def test_cyt_mcp_backend_denied_empty_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    allowed, reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "codebase-memory-mcp_query_graph",
            "tool_input": {"project": "demo", "query": "MATCH (n) RETURN n"},
        },
    )
    assert allowed is False
    assert "not injected" in reason


def test_cyt_mcp_backend_denied_turn_only_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "kind": "turn",
                "key": "turn:1",
                "prompt": "hello",
                "assistant": "hi",
            },
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    allowed, reason = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "codebase-memory-mcp_query_graph",
            "tool_input": {"project": "demo", "query": "MATCH (n) RETURN n"},
        },
    )
    assert allowed is False
    assert "not injected" in reason

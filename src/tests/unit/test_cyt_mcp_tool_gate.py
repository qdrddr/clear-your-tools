"""Unit tests for cyt-client pre-toolcall gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt_client.tool_gate import normalize_mcp_tool_name, validate_pre_tool_call


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

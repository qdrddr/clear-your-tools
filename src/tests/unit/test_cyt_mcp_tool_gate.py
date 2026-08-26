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


def _write_type2_session(
    path: Path,
    tool_name: str,
    schema: dict,
    *,
    inject_enabled: bool = True,
) -> None:
    path.write_text(
        json.dumps(
            {
                "kind": "session_state",
                "key": "session_state:inject",
                "tools_inject_enabled": inject_enabled,
            },
        )
        + "\n"
        + json.dumps(
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "test-hash",
                "tools": [
                    {
                        "name": tool_name,
                        "input_schema": schema,
                    },
                ],
            },
        )
        + "\n",
        encoding="utf-8",
    )


def _write_multi_tool_session(path: Path, tools: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "kind": "session_state",
                "key": "session_state:inject",
                "tools_inject_enabled": True,
            },
        )
        + "\n"
        + json.dumps(
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "test-hash",
                "tools": tools,
            },
        )
        + "\n",
        encoding="utf-8",
    )


def test_normalize_codex_mcp_name() -> None:
    assert normalize_mcp_tool_name("mcp__filesystem__read_file", agent="codex") == (
        "filesystem_read_file"
    )


def test_validate_denies_unknown_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_type2_session(
        log_path,
        "filesystem_read_file",
        {"type": "object", "properties": {"path": {"type": "string"}}},
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "filesystem_write_file",
        },
    )
    assert validation.allowed is False
    assert "not in cyt_mcp" in validation.reason


def test_validate_denies_unknown_tool_lists_available_and_get_tool_definitions_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_type2_session(
        log_path,
        "filesystem_read_file",
        {"type": "object", "properties": {"path": {"type": "string"}}},
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "filesystem_write_file",
        },
    )
    assert validation.allowed is False
    assert "Available tools:" in validation.reason
    assert "- filesystem_read_file" in validation.reason
    assert "get-tool-definitions" in validation.reason
    assert '{"tool_name":"filesystem_write_file"}' in validation.reason
    assert "Correct tool definition:" not in validation.reason


def test_validate_denies_bad_property_includes_minimized_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_type2_session(
        log_path,
        "filesystem_read_file",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "filesystem_read_file",
            "tool_input": {"bogus": "x"},
        },
    )
    assert validation.allowed is False
    assert "invalid cyt-mcp tool arguments" in validation.reason
    assert "Correct tool definition:" in validation.reason
    assert '"input_schema":' in validation.reason
    assert "\n  " not in validation.reason.split("Correct tool definition:", 1)[1]
    assert "Available tools:" not in validation.reason


def test_validate_allows_get_tool_definitions_without_session_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: None,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": _GET_TOOL_DEFINITIONS_TOOL,
            "tool_input": {"tool_name": "codebase-memory-mcp_search_graph"},
        },
    )
    assert validation.allowed is True
    assert validation.reason == ""


def test_validate_allows_get_tool_definitions_resolved_backend_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_type2_session(
        log_path,
        "codebase-memory-mcp_search_graph",
        {
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
        },
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "codebase-memory-mcp_search_graph",
            "tool_input": {"project": "demo"},
        },
    )
    assert validation.allowed is True


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
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": _GET_TOOL_DEFINITIONS_TOOL,
        },
    )
    assert validation.allowed is False
    assert "tool_name is required" in validation.reason


def test_validate_denies_get_tool_definitions_with_empty_tool_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: None,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": _GET_TOOL_DEFINITIONS_TOOL,
            "tool_input": {"tool_name": ""},
        },
    )
    assert validation.allowed is False
    assert "tool_name is required" in validation.reason


def test_non_cyt_mcp_tool_allowed_without_session_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: None,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "Shell",
            "tool_input": {"command": "echo hi"},
        },
    )
    assert validation.allowed is True
    assert validation.reason == ""


def test_cyt_mcp_backend_denied_without_session_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: None,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "codebase-memory-mcp_query_graph",
            "tool_input": {"project": "demo", "query": "MATCH (n) RETURN n"},
        },
    )
    assert validation.allowed is True
    assert validation.reason == ""


def test_get_tool_definitions_allowed_without_session_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: None,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": _GET_TOOL_DEFINITIONS_TOOL,
            "tool_input": {"tool_name": "codebase-memory-mcp_search_graph"},
        },
    )
    assert validation.allowed is True
    assert validation.reason == ""


def test_cyt_mcp_backend_allowed_empty_session_with_inject_flag_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "kind": "session_state",
                "key": "session_state:inject",
                "tools_inject_enabled": True,
            },
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "codebase-memory-mcp_query_graph",
            "tool_input": {"project": "demo", "query": "MATCH (n) RETURN n"},
        },
    )
    assert validation.allowed is True
    assert validation.reason == ""


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
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "codebase-memory-mcp_query_graph",
            "tool_input": {"project": "demo", "query": "MATCH (n) RETURN n"},
        },
    )
    assert validation.allowed is True
    assert validation.reason == ""


def test_validate_resolves_prefixed_tool_name_to_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_multi_tool_session(
        log_path,
        [
            {
                "name": "search_code",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "project": {"type": "string"},
                    },
                    "required": ["pattern", "project"],
                },
            },
        ],
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "codebase-memory_search_code",
            "tool_input": {"pattern": "bm25", "project": "clear-your-tools"},
        },
    )
    assert validation.allowed is True


def test_validate_denies_search_code_query_alias_without_coalescing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_multi_tool_session(
        log_path,
        [
            {
                "name": "search_code",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "project": {"type": "string"},
                    },
                    "required": ["pattern", "project"],
                },
            },
        ],
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "codebase-memory_search_code",
            "tool_input": {"query": "bm25", "limit": 10},
            "workspace_roots": ["/tmp/clear-your-tools"],
        },
    )
    assert validation.allowed is False
    assert "unknown property 'query'" in validation.reason
    assert "catalog name 'search_code'" in validation.reason
    assert "Missing required: project='clear-your-tools'" in validation.reason


def test_validate_denies_search_without_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_multi_tool_session(
        log_path,
        [
            {
                "name": "search",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "repo": {"type": "string"},
                    },
                    "required": ["query", "repo"],
                },
            },
        ],
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "semble_search",
            "tool_input": {"query": "bm25 codebase primary location"},
            "workspace_roots": ["/tmp/clear-your-tools"],
        },
    )
    assert validation.allowed is False
    assert "missing required property 'repo'" in validation.reason
    assert "Missing required: repo='/tmp/clear-your-tools'" in validation.reason


def test_validate_denies_search_without_repo_windows_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "clear-your-tools"
    repo.mkdir()
    log_path = tmp_path / "session.jsonl"
    _write_multi_tool_session(
        log_path,
        [
            {
                "name": "search",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "repo": {"type": "string"},
                    },
                    "required": ["query", "repo"],
                },
            },
        ],
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "semble_search",
            "tool_input": {"query": "bm25 codebase primary location"},
            "workspace_roots": [str(repo)],
        },
    )
    assert validation.allowed is False
    assert f"Missing required: repo={str(repo)!r}" in validation.reason


def test_validate_denies_grep_path_with_fixup_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_multi_tool_session(
        log_path,
        [
            {
                "name": "grep",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ],
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": "fff_grep",
            "tool_input": {"path": "src", "pattern": "bm25"},
        },
    )
    assert validation.allowed is False
    assert "unknown property 'path'" in validation.reason
    assert "Use query (not path)" in validation.reason

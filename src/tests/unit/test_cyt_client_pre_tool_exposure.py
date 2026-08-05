"""Tests for PreToolUse deny session log exposure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt_client.cli import _handle_pre_tool
from cyt_client.session_pre_tool_exposure import (
    build_get_tool_definitions_type1_entry,
    build_type1_tool_entry_from_catalog_record,
)
from cyt_client.sessions import read_session_log_file
from cyt_client.tool_gate import validate_pre_tool_call

_GET_TOOL_DEFINITIONS_TOOL = "cyt-mcp_get-tool-definitions"


def _write_type2_session(
    path: Path,
    tool_name: str,
    schema: dict,
    *,
    inject_enabled: bool = True,
    extra_tools: list[dict] | None = None,
) -> None:
    tools = [
        {
            "name": tool_name,
            "input_schema": schema,
        },
    ]
    if extra_tools:
        tools.extend(extra_tools)
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
                "tools": tools,
            },
        )
        + "\n",
        encoding="utf-8",
    )


def test_schema_mismatch_persists_full_type1_tool_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    _write_type2_session(log_path, "filesystem_read_file", schema)
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    monkeypatch.setattr(
        "cyt_client.session_pre_tool_exposure.session_log_path",
        lambda _payload: log_path,
    )

    payload = {
        "hook_event_name": "preToolUse",
        "session_id": "session",
        "tool_name": "filesystem_read_file",
        "tool_input": {"bogus": "x"},
    }
    with pytest.raises(SystemExit) as exc_info:
        _handle_pre_tool(payload, cursor_output=False)
    assert exc_info.value.code == 2

    _agent, entries = read_session_log_file(log_path)
    tool_entries = [entry for entry in entries if entry.get("kind") == "tool"]
    assert len(tool_entries) == 1
    entry = tool_entries[0]
    assert entry["full"] is True
    assert entry["catalog"] == "cyt_mcp"
    assert entry["name"] == "filesystem_read_file"
    assert entry["key"] == "tool:cyt_mcp:filesystem_read_file"
    assert entry["input_schema"] == schema
    expected = build_type1_tool_entry_from_catalog_record(
        {"name": "filesystem_read_file", "input_schema": schema},
        catalog="cyt_mcp",
        full=True,
    )
    assert entry["hash"] == expected["hash"]


def test_unknown_tool_persists_get_tool_definitions_entry(
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
    monkeypatch.setattr(
        "cyt_client.session_pre_tool_exposure.session_log_path",
        lambda _payload: log_path,
    )

    payload = {
        "hook_event_name": "preToolUse",
        "session_id": "session",
        "tool_name": "filesystem_write_file",
    }
    with pytest.raises(SystemExit):
        _handle_pre_tool(payload, cursor_output=False)

    _agent, entries = read_session_log_file(log_path)
    tool_entries = [entry for entry in entries if entry.get("kind") == "tool"]
    assert len(tool_entries) == 1
    entry = tool_entries[0]
    assert entry["full"] is True
    assert entry["name"] == _GET_TOOL_DEFINITIONS_TOOL
    assert entry["key"] == f"tool:cyt_mcp:{_GET_TOOL_DEFINITIONS_TOOL}"


def test_repeated_deny_does_not_duplicate_type1_entry(
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
    monkeypatch.setattr(
        "cyt_client.session_pre_tool_exposure.session_log_path",
        lambda _payload: log_path,
    )

    payload = {
        "hook_event_name": "preToolUse",
        "session_id": "session",
        "tool_name": "filesystem_write_file",
    }
    for _ in range(2):
        with pytest.raises(SystemExit):
            _handle_pre_tool(payload, cursor_output=False)

    _agent, entries = read_session_log_file(log_path)
    tool_entries = [entry for entry in entries if entry.get("kind") == "tool"]
    assert len(tool_entries) == 1


def test_validate_returns_exposure_for_schema_mismatch(
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
    assert validation.exposure is not None
    assert validation.exposure.persist == "catalog_tool"
    assert validation.exposure.tool_name == "filesystem_read_file"


def test_validate_returns_exposure_for_unknown_cyt_mcp_tool(
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
    assert validation.exposure is not None
    assert validation.exposure.persist == "get_tool_definitions"


def test_get_tool_definitions_entry_uses_catalog_when_present() -> None:
    schema = {
        "type": "object",
        "properties": {"tool_name": {"type": "string", "enum": ["a"]}},
        "required": ["tool_name"],
    }
    catalogs = {
        "tool_catalog:cyt_mcp": {
            "tools": [
                {
                    "name": _GET_TOOL_DEFINITIONS_TOOL,
                    "input_schema": schema,
                    "description": "from catalog",
                },
            ],
        },
    }
    entry = build_get_tool_definitions_type1_entry(catalogs, full=True)
    assert entry["input_schema"] == schema
    assert entry["description"] == "from catalog"


def test_schema_mismatch_persists_full_type1_from_type2_catalog_with_prefixed_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    schema = {
        "type": "object",
        "properties": {"project": {"type": "string"}},
        "required": ["project"],
    }
    catalog_tool = {
        "name": "index_status",
        "input_schema": schema,
        "description": "Get the indexing status of a project",
        "hash": "placeholder",
    }
    from cyt.injection.session_log_build import catalog_tool_record_content_hash

    catalog_tool["hash"] = catalog_tool_record_content_hash("cyt_mcp", catalog_tool)
    log_path.write_text(
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
                "tools": [catalog_tool],
            },
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
    )
    monkeypatch.setattr(
        "cyt_client.session_pre_tool_exposure.session_log_path",
        lambda _payload: log_path,
    )

    payload = {
        "hook_event_name": "preToolUse",
        "session_id": "session",
        "tool_name": "MCP:codebase-memory_index_status",
        "tool_input": {},
        "workspace_roots": ["/tmp/clear-your-tools"],
    }
    with pytest.raises(SystemExit):
        _handle_pre_tool(payload, cursor_output=False)

    _agent, entries = read_session_log_file(log_path)
    tool_entries = [entry for entry in entries if entry.get("kind") == "tool"]
    assert len(tool_entries) == 1
    entry = tool_entries[0]
    assert entry["full"] is True
    assert entry["catalog"] == "cyt_mcp"
    assert entry["name"] == "index_status"
    assert entry["key"] == "tool:cyt_mcp:index_status"
    assert entry["input_schema"] == schema
    assert entry["description"] == "Get the indexing status of a project"
    assert entry["source"] == "cyt-client_pre-tool-deny"
    assert entry["hash"] == catalog_tool["hash"]


def test_deny_type1_hash_skips_hook_reinjection() -> None:
    from cyt.injection.session_log import SessionLogIndex, resolve_injection_mode
    from cyt.injection.session_log_build import catalog_tool_record_content_hash

    schema = {
        "type": "object",
        "properties": {"project": {"type": "string"}},
        "required": ["project"],
    }
    catalog_tool = {
        "name": "index_status",
        "input_schema": schema,
        "description": "Get the indexing status of a project",
    }
    catalog_tool["hash"] = catalog_tool_record_content_hash("cyt_mcp", catalog_tool)
    deny_entry = build_type1_tool_entry_from_catalog_record(
        catalog_tool,
        catalog="cyt_mcp",
        full=True,
    )
    index = SessionLogIndex(entries=(deny_entry,))
    master_tool = {
        "tool_name": "index_status",
        "name": "codebase-memory_index_status",
        "description": catalog_tool["description"],
        "input_schema": schema,
        "cyt_catalog_source": "cyt_mcp",
    }
    from cyt.injection.session_log_build import format_tool_fragment, tool_content_hash

    current_hash = tool_content_hash(
        master_tool,
        catalog="cyt_mcp",
        catalog_tools=[master_tool],
    )
    assert current_hash == deny_entry["hash"]
    mode = resolve_injection_mode(
        key="tool:cyt_mcp:index_status",
        current_hash=current_hash,
        index=index,
        session_text="",
        formatted_skinny=format_tool_fragment(master_tool, catalog="cyt_mcp", full=False),
        formatted_full=format_tool_fragment(master_tool, catalog="cyt_mcp", full=True),
    )
    assert mode == "skip"

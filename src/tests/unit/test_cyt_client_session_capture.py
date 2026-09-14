"""Unit tests for cyt_client session capture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch
from urllib.request import Request

import pytest

from cyt_client.session_capture import (
    extract_cyt_mcp_search_result,
    is_post_tool_capture_event,
    merge_tool_into_cyt_mcp_catalog,
    persist_cyt_mcp_search_result,
    persist_turn_to_session_log,
)
from cyt_client.tool_examples_capture import notify_tool_examples_capture
from cyt_client.tool_gate import extract_post_tool_example_capture


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


def _write_cyt_mcp_session(
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
                        "server_key": "codebase-memory-mcp",
                        "tool_name": "search_graph",
                        "input_schema": schema,
                    },
                ],
            },
        )
        + "\n",
        encoding="utf-8",
    )


def test_extract_post_tool_example_capture_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    schema = {
        "type": "object",
        "properties": {"project": {"type": "string"}, "query": {"type": "string"}},
        "required": ["project"],
    }
    _write_cyt_mcp_session(log_path, "codebase-memory-mcp_search_graph", schema)
    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", lambda _payload: log_path)
    payload = {
        "hook_event_name": "postToolUse",
        "tool_name": "MCP:codebase-memory-mcp_search_graph",
        "tool_input": {"project": "demo", "query": "bm25"},
        "tool_output": json.dumps({"results": []}),
    }
    capture = extract_post_tool_example_capture(payload)
    assert capture is not None
    assert capture["mcp_server"] == "codebase-memory-mcp"
    assert capture["tool_name"] == "search_graph"
    assert capture["args"] == {"project": "demo", "query": "bm25"}


def test_extract_post_tool_example_capture_resolves_server_when_tool_name_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session logs omit tool_name when wire name equals bare name; capture must still resolve server."""
    log_path = tmp_path / "session.jsonl"
    schema = {"type": "object", "properties": {"pattern": {"type": "string"}}}
    log_path.write_text(
        json.dumps(
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "test-hash",
                "tools": [
                    {
                        "name": "grep",
                        "server_key": "fff",
                        "input_schema": schema,
                    },
                ],
            },
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", lambda _payload: log_path)
    payload = {
        "hook_event_name": "postToolUse",
        "tool_name": "MCP:grep",
        "tool_input": {"pattern": "auth"},
        "tool_output": json.dumps({"results": []}),
    }
    capture = extract_post_tool_example_capture(payload)
    assert capture is not None
    assert capture["mcp_server"] == "fff"
    assert capture["tool_name"] == "grep"


def test_extract_post_tool_example_capture_skips_failed_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "session.jsonl"
    schema = {"type": "object", "properties": {"project": {"type": "string"}}}
    _write_cyt_mcp_session(log_path, "codebase-memory-mcp_search_graph", schema)
    monkeypatch.setattr("cyt_client.tool_gate.session_log_path", lambda _payload: log_path)
    payload = {
        "hook_event_name": "postToolUse",
        "tool_name": "MCP:codebase-memory-mcp_search_graph",
        "tool_input": {"project": "demo"},
        "tool_output": json.dumps({"isError": True, "error": "boom"}),
    }
    assert extract_post_tool_example_capture(payload) is None


def test_notify_tool_examples_capture_posts_to_daemon(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    log_path = tmp_path / "session.jsonl"
    schema = {"type": "object", "properties": {"project": {"type": "string"}}}
    _write_cyt_mcp_session(log_path, "codebase-memory-mcp_search_graph", schema)
    payload = {
        "hook_event_name": "postToolUse",
        "workspace_roots": [str(tmp_path)],
        "tool_name": "MCP:codebase-memory-mcp_search_graph",
        "tool_input": {"project": "demo"},
        "tool_output": json.dumps({"ok": True}),
    }
    captured: dict[str, object] = {}

    class FakeResponse:
        def read(self) -> bytes:
            return b""

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: Request, timeout: int = 0) -> FakeResponse:
        captured["url"] = request.full_url
        captured["body"] = json.loads(cast(bytes, request.data or b"").decode())
        captured["timeout"] = timeout
        return FakeResponse()

    with (
        patch("cyt_client.tool_gate.session_log_path", return_value=log_path),
        patch(
            "cyt_client.tool_examples_capture.resolve_hook_url",
            return_value="http://127.0.0.1:9999/hook/connect",
        ),
        patch("cyt_client.tool_examples_capture.urlopen", side_effect=fake_urlopen),
    ):
        notify_tool_examples_capture(payload)

    assert captured["url"] == "http://127.0.0.1:9999/hook/tool-examples/record"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["mcp_server"] == "codebase-memory-mcp"
    assert body["tool_name"] == "search_graph"
    assert body["args"] == {"project": "demo"}


def test_notify_tool_examples_capture_skips_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"value": False}

    class FakeResponse:
        def read(self) -> bytes:
            return b""

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(*args: object, **kwargs: object) -> FakeResponse:
        called["value"] = True
        raise AssertionError("should not call urlopen")

    monkeypatch.setattr(
        "cyt_client.tool_examples_capture.tool_examples_post_tool_capture_enabled",
        lambda: False,
    )
    with patch("cyt_client.tool_examples_capture.urlopen", side_effect=fake_urlopen):
        notify_tool_examples_capture({"hook_event_name": "postToolUse"})
    assert called["value"] is False


def test_handle_post_tool_capture_invokes_examples_notify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt_client.cli import _handle_post_tool_capture

    called = {"examples": False}

    def fake_notify(payload: dict) -> None:
        called["examples"] = True

    monkeypatch.setattr(
        "cyt_client.cli.persist_cyt_mcp_search_result",
        lambda _payload: False,
    )
    monkeypatch.setattr(
        "cyt_client.tool_examples_capture.notify_tool_examples_capture",
        fake_notify,
    )
    _handle_post_tool_capture(
        {"hook_event_name": "postToolUse", "tool_name": "demo"},
        cursor_output=False,
    )
    assert called["examples"] is True


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

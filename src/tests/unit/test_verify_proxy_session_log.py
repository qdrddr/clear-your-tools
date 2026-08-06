"""Unit tests for verify-only proxy session JSONL writes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cyt.proxy.verify_session_log import (
    maybe_record_verify_proxy_request,
    resolve_proxy_user_query,
    session_id_from_headers,
)


def test_session_id_from_headers_claude() -> None:
    headers = {"x-claude-code-session-id": "sess-abc"}
    assert session_id_from_headers(headers) == "sess-abc"


def test_session_id_from_headers_codex() -> None:
    headers = {"session-id": "codex-123"}
    assert session_id_from_headers(headers) == "codex-123"


def test_resolve_proxy_user_query_anthropic() -> None:
    body = json.dumps(
        {
            "messages": [
                {"role": "user", "content": "hello from proxy"},
            ],
        },
    ).encode()
    assert resolve_proxy_user_query(body, "anthropic") == "hello from proxy"


def test_maybe_record_verify_proxy_request_writes_catalog_and_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "claude" / "cyt" / "sessions" / "sess-1.jsonl"
    log_path.parent.mkdir(parents=True)

    config = {
        "hallucination_gate": {"enabled": True},
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
            "tools": {"enabled": False},
        },
        "skills": {"enabled": False},
    }

    tools = [
        {
            "name": "cyt-mcp_demo_tool",
            "description": "demo",
            "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}},
        },
    ]

    monkeypatch.setattr(
        "cyt.proxy.verify_session_log._resolve_verify_tools",
        lambda _cfg, _input: tools,
    )

    with patch("cyt_client.sessions.session_log_path", return_value=log_path):
        maybe_record_verify_proxy_request(
            headers={"x-claude-code-session-id": "sess-1", "session-id": "ignored"},
            agent="claude",
            config=config,
            input_tools=tools,
            original_body=json.dumps(
                {"messages": [{"role": "user", "content": "turn one"}]},
            ).encode(),
            kind="anthropic",
        )

    assert log_path.is_file()
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    kinds = [entry.get("kind") for entry in lines]
    assert "session_state" in kinds
    assert "tool_catalog" in kinds
    assert "tool" in kinds
    assert "turn" in kinds
    turn = next(entry for entry in lines if entry.get("kind") == "turn")
    assert turn.get("prompt") == "turn one"
    assert turn.get("assistant") == ""


def test_maybe_record_verify_proxy_request_skips_hook_agent() -> None:
    config = {
        "hallucination_gate": {"enabled": True},
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
            "tools": {"enabled": False},
        },
        "skills": {"enabled": False},
    }
    append_mock = MagicMock()
    with patch("cyt.injection.verify_session_log.append_verify_session_log", append_mock):
        maybe_record_verify_proxy_request(
            headers={"x-claude-code-session-id": "sess-1"},
            agent="cursor",
            config=config,
            input_tools=[{"name": "demo"}],
            original_body=b"{}",
            kind="anthropic",
        )
    append_mock.assert_not_called()
